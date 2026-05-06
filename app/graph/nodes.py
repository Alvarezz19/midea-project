from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from app.graph.state import AgentState
from app.services.design_brief import build_design_brief, selected_design_brief_for_state
from app.services.json_project import create_project_version, create_project_version_from_nodes, load_project
from app.services.patch_engine import PatchEngineError, dry_run_patch
from app.services.planner import is_structural_change_intent, plan_patch_request
from app.services.llm_planner import LLMPlannerError
from app.services.planner_execution import PlannerDryRunFeedbackError, plan_patch_with_llm_dry_run_feedback
from app.services.project_semantic_index import summarize_affected_nodes
from app.services.requirement_conformance import build_requirement_conformance_report, merge_conformance_into_validation
from app.services.requirement_analysis import analyze_requirement, explain_template_candidate
from app.services.requirement_extractor import RequirementExtractionError, extract_requirement_with_llm
from app.services.requirement_model import merge_requirement_slots, requirement_summary_from_slots
from app.services.retrieval import RetrievalError, get_template_by_id, normalize_project_type, search_templates
from app.services.runtime_store import postgres_runtime_enabled
from app.services.validator import validate_project


def classify_project_type(state: AgentState) -> dict[str, Any]:
    project_type = state.get("project_type")
    if not project_type:
        project_type = normalize_project_type(_last_user_content(state))
    return {
        "project_type": project_type,
        "status": "project_type_classified" if project_type else "need_project_type",
        "next_action": None if project_type else "ask_project_type",
    }


def collect_requirements(state: AgentState) -> dict[str, Any]:
    user_text = _last_user_content(state)
    rule_summary = analyze_requirement(
        user_text,
        existing=state.get("requirement_summary"),
        project_type=state.get("project_type"),
    )
    llm_result = _try_extract_requirement_with_llm(user_text, provider=state.get("llm_provider"))
    slots = merge_requirement_slots(
        message=user_text,
        rule_summary=rule_summary,
        llm_result=llm_result,
        existing_slots=state.get("requirement_slots"),
        current_project_path=state.get("current_project_path"),
    )
    summary = requirement_summary_from_slots(slots)
    status = "requirements_collected" if summary.get("ready_for_template_search") else "awaiting_requirement_clarification"
    next_action = None if summary.get("ready_for_template_search") else "clarify_requirements"
    if "project_type" in summary.get("blocking_missing_fields", []):
        status = "need_project_type"
        next_action = "ask_project_type"
    return {
        "project_type": summary.get("project_type"),
        "requirement_summary": summary,
        "requirement_slots": slots,
        "open_questions": summary.get("open_questions", []),
        "confirmed_requirements": summary.get("confirmed_requirements", []),
        "status": status,
        "next_action": next_action,
    }


def _try_extract_requirement_with_llm(message: str, *, provider: str | None) -> dict[str, Any] | None:
    try:
        return extract_requirement_with_llm(message, provider=provider)
    except RequirementExtractionError:
        return None


def retrieve_template_candidates(state: AgentState) -> dict[str, Any]:
    project_type = state.get("project_type")
    if not project_type:
        return {"template_candidates": [], "design_brief": None, "status": "need_project_type", "next_action": "ask_project_type"}

    query = _requirement_query(state)
    candidates = search_templates(query, project_type=project_type, limit=3)
    if not candidates:
        return {"template_candidates": [], "design_brief": None, "status": "no_template_candidate", "next_action": "ask_more_requirements"}
    requirement_summary = state.get("requirement_summary") or {}
    slim_candidates = [_slim_template_candidate(candidate, requirement_summary=requirement_summary) for candidate in candidates]
    design_brief = build_design_brief(
        requirement_slots=state.get("requirement_slots"),
        requirement_summary=requirement_summary,
        template_candidates=slim_candidates,
    )
    return {
        "template_candidates": slim_candidates,
        "design_brief": design_brief,
        "status": "template_candidates_ready",
        "next_action": "confirm_template",
    }


def select_or_wait_template(state: AgentState) -> dict[str, Any]:
    selected_template_id = state.get("selected_template_id")
    candidates = state.get("template_candidates") or []
    if state.get("status") in {"need_project_type", "no_template_candidate"}:
        return {"status": state.get("status"), "next_action": state.get("next_action")}

    if selected_template_id:
        next_state = dict(state)
        next_state["selected_template_id"] = selected_template_id
        return {
            "selected_template_id": selected_template_id,
            "design_brief": selected_design_brief_for_state(next_state),
            "status": "template_selected",
            "next_action": None,
        }

    if state.get("auto_confirm_template") and candidates:
        next_state = dict(state)
        next_state["selected_template_id"] = candidates[0]["template_id"]
        return {
            "selected_template_id": candidates[0]["template_id"],
            "design_brief": selected_design_brief_for_state(next_state),
            "status": "template_selected",
            "next_action": None,
        }

    return _with_assistant_summary(
        state,
        {
            "status": "awaiting_template_confirmation",
            "next_action": "confirm_template",
        },
    )


def confirm_template_interrupt_node(state: AgentState) -> dict[str, Any]:
    candidates = state.get("template_candidates") or []
    candidate_ids = {str(candidate.get("template_id")) for candidate in candidates if candidate.get("template_id")}
    if not candidates:
        return {"status": "no_template_candidate", "next_action": "ask_more_requirements"}

    resume_value = interrupt(
        {
            "kind": "template_confirmation",
            "question": "请选择要使用的模板。",
            "template_candidates": candidates,
        }
    )
    selected_template_id = _selected_template_from_resume(resume_value)
    if selected_template_id not in candidate_ids:
        return {
            "status": "awaiting_template_confirmation",
            "next_action": "confirm_template",
            "error": f"模板不在候选列表中: {selected_template_id}",
        }
    return {
        "selected_template_id": selected_template_id,
        "design_brief": build_design_brief(
            requirement_slots=state.get("requirement_slots"),
            requirement_summary=state.get("requirement_summary") or {},
            template_candidates=candidates,
            selected_template_id=selected_template_id,
        ),
        "status": "template_selected",
        "next_action": None,
        "error": None,
    }


def _selected_template_from_resume(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        selected = value.get("selected_template_id") or value.get("template_id")
        if isinstance(selected, str):
            return selected
    return ""


def _with_assistant_summary(state: AgentState, updates: dict[str, Any]) -> dict[str, Any]:
    next_state = dict(state)
    next_state.update(updates)
    messages = list(state.get("messages") or [])
    messages.append({"role": "assistant", "content": _build_assistant_summary(next_state)})
    return {**updates, "messages": messages}


def confirm_patch_interrupt_node(state: AgentState) -> dict[str, Any]:
    pending_patch = state.get("pending_confirmation_patch")
    if not isinstance(pending_patch, dict):
        return {"status": "ready_for_user_patch", "next_action": "wait_user_patch"}

    risk_assessment = state.get("risk_assessment")
    resume_value = interrupt(
        {
            "kind": "patch_confirmation",
            "question": "补丁 dry-run 已通过，是否确认应用？",
            "pending_patch": pending_patch,
            "risk_assessment": risk_assessment,
            "confirmation_summary": risk_assessment.get("confirmation_summary") if isinstance(risk_assessment, dict) else None,
            "operation_summaries": risk_assessment.get("operation_summaries") if isinstance(risk_assessment, dict) else [],
            "dry_run": state.get("planner_dry_run"),
            "current_project_version_id": state.get("current_project_version_id"),
        }
    )
    action = _confirmation_action_from_resume(resume_value)
    if action == "cancel":
        return {
            "pending_patch": None,
            "pending_confirmation_patch": None,
            "patch_confirmation": {
                "action": "cancelled",
                "cancelled_at": _utc_now_iso(),
                "risk_assessment": state.get("risk_assessment"),
            },
            "status": "patch_confirmation_cancelled",
            "next_action": "send_message",
            "error": None,
        }
    if action != "approve":
        return {
            "status": "awaiting_patch_confirmation",
            "next_action": "confirm_patch",
            "error": f"未知补丁确认动作: {action}",
        }
    return {
        "pending_patch": pending_patch,
        "patch_confirmation": {
            "action": "approved",
            "confirmed_patch": pending_patch,
            "confirmed_project_version_id": state.get("current_project_version_id"),
            "confirmed_at": _utc_now_iso(),
            "risk_assessment": state.get("risk_assessment"),
        },
        "status": "patch_confirmation_approved",
        "next_action": None,
        "error": None,
    }


def _confirmation_action_from_resume(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        action = value.get("action")
        if isinstance(action, str):
            return action
    return ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def route_after_template_confirmation(state: AgentState) -> str:
    if state.get("status") == "template_selected":
        return "create_project_version"
    return "summarize_result"


def route_after_patch_application(state: AgentState) -> str:
    if state.get("status") == "awaiting_patch_confirmation" and state.get("next_action") == "confirm_patch":
        return "confirm_patch_interrupt"
    return "summarize_result"


def route_after_patch_confirmation(state: AgentState) -> str:
    if state.get("status") == "patch_confirmation_approved":
        return "apply_pending_patch"
    return "summarize_result"


def _await_patch_confirmation_update(state: AgentState, pending_patch: dict[str, Any], result: dict[str, Any], risk_assessment: dict[str, Any]) -> dict[str, Any]:
    dry_run_preview = dict(result)
    dry_run_preview.pop("nodes", None)
    return _with_assistant_summary(
        state,
        {
            "pending_patch": None,
            "pending_confirmation_patch": pending_patch,
            "planner_dry_run": dry_run_preview,
            "risk_assessment": risk_assessment,
            "validation_report": result["validation_report"],
            "patch_result": None,
            "status": "awaiting_patch_confirmation",
            "next_action": "confirm_patch",
        },
    )


def _is_approved_resume_state(state: AgentState, pending_patch: dict[str, Any]) -> bool:
    return _is_patch_confirmed(state, pending_patch)


def create_project_version_node(state: AgentState) -> dict[str, Any]:
    if state.get("current_project_path"):
        return {
            "status": "project_version_ready",
            "next_action": None,
            "project_created_in_current_run": False,
        }

    selected_template_id = state.get("selected_template_id")
    if not selected_template_id:
        return {"status": "awaiting_template_confirmation", "next_action": "confirm_template"}

    try:
        template = get_template_by_id(selected_template_id)
        metadata = create_project_version(
            template["source_path"],
            versions_dir=state.get("versions_dir", "projects/versions"),
            note="由 LangGraph 最小工作流创建。",
            project_type=str(state.get("project_type") or template.get("project_type") or "ahu"),
            project_name=str(template.get("file_name") or selected_template_id),
            source_template_id=str(selected_template_id),
            requirement_slots=state.get("requirement_slots"),
            design_brief=state.get("design_brief") or selected_design_brief_for_state(state),
            conformance_report=state.get("conformance_report"),
        )
    except (RetrievalError, ValueError) as exc:
        return {"status": "error", "error": str(exc), "next_action": "fix_template_selection"}

    return {
        "current_project_id": metadata["project_id"],
        "current_project_version_id": metadata["version_id"],
        "current_project_path": metadata["version_path"],
        "status": "project_version_ready",
        "next_action": None,
        "project_created_in_current_run": True,
    }


def plan_patch_node(state: AgentState) -> dict[str, Any]:
    project_path = state.get("current_project_path")
    if not project_path:
        return {"status": "error", "error": "无法规划补丁：current_project_path 为空。", "next_action": "fix_project_version"}

    message = _last_user_content(state)
    memory_update = _recent_user_intent_update(state, message)
    result = plan_patch_request(
        message,
        project_path=project_path,
        template_id=state.get("selected_template_id"),
        project_type=state.get("project_type"),
    )
    if result.get("status") == "planned":
        return {
            **memory_update,
            "planner_result": result,
            "pending_patch": result.get("pending_patch"),
            "status": "patch_planned",
            "next_action": None,
        }
    if _should_fallback_to_llm_planner(message, result, state):
        return {**memory_update, **_plan_patch_with_llm_node(state, project_path, rule_result=result)}
    return {
        **memory_update,
        "planner_result": result,
        "status": "awaiting_patch_clarification",
        "next_action": "clarify_patch",
    }


def _should_fallback_to_llm_planner(message: str, rule_result: dict[str, Any], state: AgentState) -> bool:
    if rule_result.get("status") == "planned":
        return False
    return is_structural_change_intent(message) or bool(state.get("use_llm_planner"))


def _plan_patch_with_llm_node(state: AgentState, project_path: str, *, rule_result: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        result = plan_patch_with_llm_dry_run_feedback(
            _last_user_content(state),
            project_path=project_path,
            template_id=state.get("selected_template_id"),
            project_type=state.get("project_type"),
            provider=state.get("llm_provider"),
            llm_max_attempts=int(state.get("llm_max_attempts") or 2),
        )
    except LLMPlannerError as exc:
        question = _llm_fallback_question(str(exc), rule_result=rule_result)
        return {
            "planner_result": {
                "status": "needs_clarification",
                "planner": "llm",
                "fallback_from": "rule",
                "rule_planner_result": rule_result,
                "questions": [question],
            },
            "pending_patch": None,
            "planner_dry_run": None,
            "planner_attempts": [],
            "status": "awaiting_patch_clarification",
            "next_action": "clarify_patch",
        }
    except PlannerDryRunFeedbackError as exc:
        payload = exc.payload
        last_error = str(payload.get("last_error") or payload.get("message") or exc)
        planner_result = payload.get("planner_result")
        if not isinstance(planner_result, dict):
            planner_result = {
                "status": "needs_clarification",
                "planner": "llm",
                "questions": [_llm_fallback_question(last_error, rule_result=rule_result)],
            }
        elif not planner_result.get("questions"):
            planner_result = {**planner_result, "questions": [_llm_fallback_question(last_error, rule_result=rule_result)]}
        planner_result = {**planner_result, "fallback_from": "rule", "rule_planner_result": rule_result}
        return {
            "planner_result": planner_result,
            "pending_patch": None,
            "planner_dry_run": None,
            "planner_attempts": payload.get("planner_attempts", []),
            "status": "awaiting_patch_clarification",
            "next_action": "clarify_patch",
            "error": last_error,
        }

    if result.get("status") == "dry_run_valid":
        planner_result = result.get("planner_result")
        if isinstance(planner_result, dict) and rule_result is not None:
            planner_result = {**planner_result, "fallback_from": "rule", "rule_planner_result": rule_result}
        risk_level = planner_result.get("risk_level") if isinstance(planner_result, dict) else None
        if risk_level != "low":
            pending_patch = result.get("pending_patch")
            risk_assessment = _assess_patch_risk(pending_patch, planner_result=planner_result, dry_run=result.get("dry_run"))
            return {
                "planner_result": planner_result,
                "pending_patch": None,
                "pending_confirmation_patch": pending_patch,
                "planner_dry_run": result.get("dry_run"),
                "planner_attempts": result.get("planner_attempts", []),
                "risk_assessment": risk_assessment,
                "status": "awaiting_patch_confirmation",
                "next_action": "confirm_patch",
            }
        risk_assessment = _assess_patch_risk(result.get("pending_patch"), planner_result=planner_result, dry_run=result.get("dry_run"))
        return {
            "planner_result": planner_result,
            "pending_patch": result.get("pending_patch"),
            "pending_confirmation_patch": None,
            "planner_dry_run": result.get("dry_run"),
            "planner_attempts": result.get("planner_attempts", []),
            "risk_assessment": risk_assessment,
            "status": "patch_planned",
            "next_action": None,
        }
    return {
        "planner_result": result.get("planner_result"),
        "pending_patch": None,
        "planner_dry_run": result.get("dry_run"),
        "planner_attempts": result.get("planner_attempts", []),
        "status": "awaiting_patch_clarification",
        "next_action": "clarify_patch",
    }


def _llm_fallback_question(error: str, *, rule_result: dict[str, Any] | None) -> str:
    rule_questions = rule_result.get("questions") if isinstance(rule_result, dict) else None
    rule_text = "；".join(str(question) for question in rule_questions or [] if str(question))
    suffix = f" 规则 planner 的追问：{rule_text}" if rule_text else ""
    return (
        "当前修改涉及结构改造，已自动尝试 LLM planner dry-run fallback，但暂时无法生成可执行计划。"
        f"原因：{error}。请补充目标页面、节点名称或业务对象、接线位置、点位含义和值，或配置可用的 LLM planner 后重试。{suffix}"
    )


def apply_pending_patch_node(state: AgentState) -> dict[str, Any]:
    pending_patch = state.get("pending_patch")
    project_path = state.get("current_project_path")
    if not pending_patch:
        return {"status": "ready_for_user_patch", "next_action": "wait_user_patch"}
    if not project_path:
        return {"status": "error", "error": "存在 pending_patch，但 current_project_path 为空。", "next_action": "fix_project_version"}

    try:
        nodes = load_project(project_path)
        result = dry_run_patch(nodes, pending_patch)
        report = result["validation_report"]
        planner_result = _matching_planner_result(state.get("planner_result"), pending_patch)
        risk_assessment = _assess_patch_risk(pending_patch, planner_result=planner_result, dry_run=result)
        if report["valid"] and risk_assessment["requires_confirmation"] and not _is_approved_resume_state(state, pending_patch):
            return _await_patch_confirmation_update(state, pending_patch, result, risk_assessment)
        if report["valid"]:
            if not state.get("current_project_id"):
                return {"status": "error", "error": "无法创建补丁版本：current_project_id 为空。", "next_action": "fix_project_version"}
            metadata = create_project_version_from_nodes(
                result["nodes"],
                project_id=str(state["current_project_id"]),
                parent_version_id=state.get("current_project_version_id"),
                versions_dir=state.get("versions_dir", "projects/versions"),
                source_template_path=_selected_template_source_path(state),
                patch_summary={"changed": result["changed"], "changes": result["changes"], "diff_summary": result["diff"]["summary"]},
                validation_report=report,
                patch=pending_patch,
                risk_level=str(risk_assessment.get("risk_level") or "low"),
                request_message=_last_user_content(state),
                patch_result={"changed": result["changed"], "changes": result["changes"], "diff": result["diff"]},
                note="由结构化补丁创建。",
                requirement_slots=state.get("requirement_slots"),
                design_brief=state.get("design_brief"),
                conformance_report=state.get("conformance_report"),
            )
    except (PatchEngineError, ValueError) as exc:
        return {"status": "patch_failed", "error": str(exc), "next_action": "revise_patch"}

    response: dict[str, Any] = {
        "patch_result": {"changed": result["changed"], "changes": result["changes"], "diff": result["diff"]},
        "validation_report": report,
        "pending_patch": None,
        "pending_confirmation_patch": None,
        "risk_assessment": risk_assessment,
        "status": "patch_applied" if report["valid"] else "validation_failed",
        "next_action": None if report["valid"] else "revise_patch",
    }
    if report["valid"]:
        affected_node_ids = _affected_node_ids(result)
        last_touched_entities = summarize_affected_nodes(metadata["version_path"], affected_node_ids)
        response.update(
            {
                "current_project_version_id": metadata["version_id"],
                "current_project_path": metadata["version_path"],
                "last_affected_node_ids": affected_node_ids,
                "last_touched_entities": last_touched_entities,
                "last_patch_summary": _build_last_patch_summary(
                    state,
                    pending_patch=pending_patch,
                    result=result,
                    risk_assessment=risk_assessment,
                    affected_node_ids=affected_node_ids,
                    last_touched_entities=last_touched_entities,
                    project_version_id=metadata["version_id"],
                ),
            }
        )
    return response


def validate_current_project_node(state: AgentState) -> dict[str, Any]:
    project_path = state.get("current_project_path")
    if not project_path:
        return {"status": state.get("status", "no_project_version"), "next_action": state.get("next_action")}

    try:
        nodes = load_project(project_path)
        report = _validate_project_with_conformance(nodes, state)
        if postgres_runtime_enabled() and state.get("current_project_id"):
            from app.services.postgres_runtime import record_project_validation

            record_project_validation(
                str(state["current_project_id"]),
                state.get("current_project_version_id"),
                report,
            )
    except ValueError as exc:
        return {"status": "validation_failed", "error": str(exc), "next_action": "fix_project_json"}
    return {
        "validation_report": report,
        "conformance_report": report.get("conformance_report"),
        "status": state.get("status") if report["valid"] else "validation_failed",
        "next_action": state.get("next_action") if report["valid"] else "fix_project_json",
    }


def _validate_project_with_conformance(nodes: list[dict[str, Any]], state: AgentState) -> dict[str, Any]:
    report = validate_project(nodes)
    conformance = build_requirement_conformance_report(
        nodes=nodes,
        requirement_slots=state.get("requirement_slots"),
        design_brief=state.get("design_brief"),
    )
    return merge_conformance_into_validation(report, conformance)


def summarize_result_node(state: AgentState) -> dict[str, Any]:
    messages = list(state.get("messages") or [])
    summary = _build_assistant_summary(state)
    messages.append({"role": "assistant", "content": summary})
    return {"messages": messages}


def route_after_template_selection(state: AgentState) -> str:
    if state.get("status") == "template_selected":
        return "create_project_version"
    if state.get("status") == "awaiting_template_confirmation" and state.get("next_action") == "confirm_template":
        return "confirm_template_interrupt"
    return "summarize_result"


def route_after_requirements(state: AgentState) -> str:
    if state.get("status") in {"awaiting_requirement_clarification", "need_project_type"}:
        return "summarize_result"
    if state.get("current_project_path"):
        return "create_project_version"
    return "retrieve_template_candidates"


def route_after_project_version(state: AgentState) -> str:
    if state.get("pending_patch"):
        return "apply_pending_patch"
    if state.get("current_project_path") and not state.get("project_created_in_current_run"):
        return "plan_patch"
    return "validate_current_project"


def route_after_patch_planning(state: AgentState) -> str:
    if state.get("pending_patch"):
        return "apply_pending_patch"
    return "summarize_result"


def _last_user_content(state: AgentState) -> str:
    for message in reversed(state.get("messages") or []):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _recent_user_intent_update(state: AgentState, message: str) -> dict[str, Any]:
    if not message.strip():
        return {}
    intents = list(state.get("recent_user_intents") or [])
    intent = {
        "message": message,
        "created_at": _utc_now_iso(),
        "project_version_id": state.get("current_project_version_id"),
        "project_path": state.get("current_project_path"),
    }
    if intents and intents[-1].get("message") == message and intents[-1].get("project_version_id") == intent["project_version_id"]:
        return {"recent_user_intents": intents[-5:]}
    return {"recent_user_intents": (intents + [intent])[-5:]}


def _affected_node_ids(result: dict[str, Any]) -> list[str]:
    diff = result.get("diff") if isinstance(result.get("diff"), dict) else {}
    affected = diff.get("affected_node_ids") if isinstance(diff, dict) else []
    if not isinstance(affected, list):
        return []
    return [str(node_id) for node_id in affected if isinstance(node_id, str) and node_id]


def _build_last_patch_summary(
    state: AgentState,
    *,
    pending_patch: dict[str, Any],
    result: dict[str, Any],
    risk_assessment: dict[str, Any],
    affected_node_ids: list[str],
    last_touched_entities: list[dict[str, Any]],
    project_version_id: str | None,
) -> dict[str, Any]:
    diff = result.get("diff") if isinstance(result.get("diff"), dict) else {}
    return {
        "message": _last_user_content(state),
        "created_at": _utc_now_iso(),
        "project_version_id": project_version_id,
        "operations": _normalize_patch_operations(pending_patch),
        "change_count": len(result.get("changes") or []),
        "diff_summary": diff.get("summary") if isinstance(diff, dict) else None,
        "affected_node_ids": affected_node_ids,
        "touched_entities": last_touched_entities,
        "risk_level": risk_assessment.get("risk_level"),
    }


def _requirement_query(state: AgentState) -> str:
    summary = state.get("requirement_summary") or {}
    raw = summary.get("raw_requirements")
    if isinstance(raw, list) and raw:
        return "\n".join(str(item) for item in raw)
    return _last_user_content(state)


def _slim_template_candidate(candidate: dict[str, Any], *, requirement_summary: dict[str, Any]) -> dict[str, Any]:
    explanation = explain_template_candidate(candidate, requirement_summary)
    return {
        "template_id": candidate["template_id"],
        "project_type": candidate["project_type"],
        "project_type_label": candidate["project_type_label"],
        "source_path": candidate["source_path"],
        "file_name": candidate["file_name"],
        "node_count": candidate["node_count"],
        "tab_count": candidate["tab_count"],
        "tabs": candidate["tabs"],
        "score": candidate["score"],
        "reasons": candidate["reasons"],
        "summary": candidate["summary"],
        "matched_items": explanation["matched_items"],
        "missing_items": explanation["missing_items"],
        "estimated_modification_cost": explanation["estimated_modification_cost"],
        "risk_points": explanation["risk_points"],
        "recommendation_reasons": _candidate_recommendation_reasons(candidate, explanation),
    }


def _candidate_recommendation_reasons(candidate: dict[str, Any], explanation: dict[str, Any]) -> list[str]:
    reasons = [str(item) for item in candidate.get("reasons") or [] if str(item)]
    if explanation.get("matched_items"):
        reasons.append("已覆盖：" + "、".join(explanation["matched_items"][:5]))
    cost = explanation.get("estimated_modification_cost") or {}
    for reason in cost.get("reasons") or []:
        if str(reason):
            reasons.append(str(reason))
    result: list[str] = []
    for reason in reasons:
        if reason not in result:
            result.append(reason)
    return result


def _selected_template_source_path(state: AgentState) -> str | None:
    selected_template_id = state.get("selected_template_id")
    for candidate in state.get("template_candidates") or []:
        if candidate.get("template_id") == selected_template_id:
            source_path = candidate.get("source_path")
            return str(source_path) if source_path else None
    return None


def _build_assistant_summary(state: AgentState) -> str:
    status = state.get("status")
    if status == "need_project_type":
        return "请先确认项目类型：机房群控程序或 AHU 程序。"
    if status == "awaiting_requirement_clarification":
        questions = state.get("open_questions") or []
        question_texts = [str(item.get("question")) for item in questions if isinstance(item, dict) and item.get("question")]
        return "需要先补充需求：" + "；".join(question_texts or ["请补充项目类型和主要设备/控制目标。"])
    if status == "awaiting_template_confirmation":
        candidates = state.get("template_candidates") or []
        lines = ["已找到候选模板，请确认使用哪一个："]
        design_brief = state.get("design_brief") or {}
        if design_brief.get("summary"):
            lines.append(f"设计摘要：{design_brief['summary']}")
        for reason in (design_brief.get("recommendation_reasons") or [])[:2]:
            lines.append(f"推荐理由：{reason}")
        for index, candidate in enumerate(candidates, start=1):
            cost = candidate.get("estimated_modification_cost") or {}
            missing = candidate.get("missing_items") or []
            missing_text = "；缺失：" + "、".join(missing) if missing else ""
            lines.append(
                f"{index}. {candidate['file_name']}，得分 {candidate['score']}，改造成本 {cost.get('level', 'unknown')}，{candidate['summary']}{missing_text}"
            )
        return "\n".join(lines)
    if status == "project_version_ready":
        return f"已创建工程版本：{state.get('current_project_version_id')}，路径：{state.get('current_project_path')}。"
    if status == "ready_for_user_patch":
        return f"工程版本已就绪，等待用户提出局部修改需求。路径：{state.get('current_project_path')}。"
    if status == "awaiting_patch_clarification":
        planner_result = state.get("planner_result") or {}
        questions = planner_result.get("questions") or ["请补充修改目标。"]
        return "需要补充信息：" + "；".join(str(question) for question in questions)
    if status == "awaiting_patch_confirmation":
        planner_result = state.get("planner_result") or {}
        risk_assessment = state.get("risk_assessment") or {}
        risk_level = risk_assessment.get("risk_level") or planner_result.get("risk_level", "unknown")
        dry_run = state.get("planner_dry_run") or {}
        diff = dry_run.get("diff") or {}
        summary = diff.get("summary") or {}
        confirmation_summary = risk_assessment.get("confirmation_summary")
        if confirmation_summary:
            return f"补丁 dry-run 已通过，风险等级 {risk_level}，影响节点 {summary.get('affected_node_count', 0)} 个。{confirmation_summary}"
        return f"补丁 dry-run 已通过，风险等级 {risk_level}，影响节点 {summary.get('affected_node_count', 0)} 个，需要确认后再应用。"
    if status == "patch_confirmation_cancelled":
        return "已取消待确认补丁，当前工程版本未变化。"
    if status == "patch_applied":
        patch_result = state.get("patch_result") or {}
        changes = patch_result.get("changes") or []
        touched = state.get("last_touched_entities") or []
        if touched:
            names = "、".join(str(item.get("display_name")) for item in touched[:3] if isinstance(item, dict) and item.get("display_name"))
            if names:
                return f"补丁已应用并校验通过，共 {len(changes)} 项变更。本次定位目标：{names}。"
        return f"补丁已应用并校验通过，共 {len(changes)} 项变更。"
    if status == "validation_failed":
        report = state.get("validation_report") or {}
        return f"工程校验失败：errors={report.get('error_count')}，warnings={report.get('warning_count')}。"
    if status in {"patch_failed", "error"}:
        return f"处理失败：{state.get('error')}"
    return f"当前状态：{status}。"


def _assess_patch_risk(patch: Any, *, planner_result: Any = None, dry_run: Any = None) -> dict[str, Any]:
    planner_risk = planner_result.get("risk_level") if isinstance(planner_result, dict) else None
    operations = _normalize_patch_operations(patch)
    risk_level = planner_risk if planner_risk in {"low", "medium", "high"} else "low"
    reasons: list[str] = []
    operation_summaries: list[dict[str, Any]] = []
    dry_run_changes = dry_run.get("changes") if isinstance(dry_run, dict) and isinstance(dry_run.get("changes"), list) else []

    for operation in operations:
        op = operation.get("op")
        if op in {"add_node_from_schema", "connect", "disconnect", "enable_dynamic_input", "copy_block", "delete_node", "delete_block", "set_io_point"}:
            risk_level = _max_risk(risk_level, "medium")
            _append_unique(reasons, f"{op} 属于需要确认的结构或连线变更。")
        if op in {"copy_block", "delete_node", "delete_block", "set_io_point"}:
            risk_level = _max_risk(risk_level, "high")
        if op == "update_param":
            params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
            risky_fields = [field for field in params if _is_risky_field(str(field))]
            if risky_fields:
                risk_level = _max_risk(risk_level, "high")
                _append_unique(reasons, f"修改疑似 IO/通讯字段：{', '.join(risky_fields[:5])}。")
        operation_summaries.append(_operation_confirmation_summary(operation, dry_run_changes))

    requires_confirmation = risk_level in {"medium", "high"}
    if isinstance(planner_result, dict):
        for reason in planner_result.get("risk_reasons") or []:
            if str(reason):
                _append_unique(reasons, str(reason))
    if planner_risk in {"medium", "high"} and not reasons:
        _append_unique(reasons, "LLM planner 将该计划标记为中高风险。")
    confirmation_summary = _confirmation_summary_text(risk_level, operation_summaries, reasons)
    return {
        "risk_level": risk_level,
        "requires_confirmation": requires_confirmation,
        "reasons": reasons,
        "operation_count": len(operations),
        "operation_summaries": operation_summaries,
        "confirmation_summary": confirmation_summary,
    }


def _operation_confirmation_summary(operation: dict[str, Any], dry_run_changes: list[Any]) -> dict[str, Any]:
    op = str(operation.get("op") or "unknown")
    matching_changes = [change for change in dry_run_changes if isinstance(change, dict) and change.get("op") == op]
    summary = _operation_summary_text(operation, matching_changes)
    confirmation_points = _confirmation_points_for_operation(op)
    return {
        "op": op,
        "risk_level": _operation_risk_level(operation),
        "summary": summary,
        "confirmation_points": confirmation_points,
        "change_count": len(matching_changes),
        "changes": _compact_changes_for_confirmation(op, matching_changes),
    }


def _operation_summary_text(operation: dict[str, Any], changes: list[dict[str, Any]]) -> str:
    op = str(operation.get("op") or "unknown")
    if op == "copy_block":
        change = changes[0] if changes else {}
        copied_count = change.get("copied_node_count")
        boundary_count = change.get("boundary_connection_count")
        block_id = operation.get("block_id") or operation.get("source_block_id")
        target = operation.get("target_tab_selector")
        return f"复制功能块 {block_id} 到 {target}，复制节点 {copied_count if copied_count is not None else '待 dry-run 确认'} 个，边界接线 {boundary_count if boundary_count is not None else 0} 条。"
    if op == "set_io_point":
        parts = []
        for change in changes[:5]:
            parts.append(f"{change.get('node_id')}.{change.get('field')}: {change.get('old_value')} -> {change.get('new_value')}")
        return "修改 IO/通讯点位：" + ("；".join(parts) if parts else str(operation.get("params") or {}))
    if op == "connect":
        if changes:
            return "新增连线：" + "；".join(
                f"{change.get('source_node_id')}:{change.get('source_output')} -> {change.get('target_node_id')}:{change.get('target_input')}"
                for change in changes[:5]
            )
        return f"新增连线：{operation.get('source_node_selector')} -> {operation.get('target_node_selector')}。"
    if op == "disconnect":
        if changes:
            return "断开连线：" + "；".join(
                f"{change.get('source_node_id')}:{change.get('source_output')} -> {change.get('target_node_id')}:{change.get('target_input')}"
                for change in changes[:5]
            )
        return f"断开目标输入：{operation.get('target_node_selector')} input={operation.get('target_input')}。"
    if op == "enable_dynamic_input":
        return f"启用动态输入：{operation.get('node_selector')}，选项 {operation.get('input_option') or operation.get('input_options') or '默认'}。"
    if op == "add_node_from_schema":
        return f"新增 schema 节点：{operation.get('module_type') or operation.get('schema_selector')} 到 {operation.get('tab_selector')}。"
    if op == "add_tab":
        return f"新增页面：{operation.get('label') or operation.get('name')}。"
    if op == "replace_constant":
        return f"替换设定/常量：{operation.get('node_selector')} 的 {operation.get('field') or '默认字段'} 改为 {operation.get('value')}。"
    if op == "update_param":
        return f"修改参数：{operation.get('node_selector')} -> {operation.get('params')}。"
    if op == "rename_node":
        return f"节点改名：{operation.get('node_selector')} -> {operation.get('new_name')}。"
    if op == "add_comment":
        return f"新增备注到 {operation.get('tab_selector')}。"
    return f"{op}: {operation}"


def _confirmation_points_for_operation(op: str) -> list[str]:
    if op == "copy_block":
        return ["确认复制来源和目标页面", "确认外部入口/出口边界", "确认复制节点不复用原 BACnet 对象号"]
    if op == "set_io_point":
        return ["确认点表字段和值", "确认通道、地址或对象号不冲突", "确认修改符合现场接线/通讯表"]
    if op == "connect":
        return ["确认连线方向", "确认源输出端和目标输入端", "确认不会旁路保护联锁"]
    if op == "disconnect":
        return ["确认断开的目标输入端", "确认不会切断保护、反馈或故障链路"]
    if op == "enable_dynamic_input":
        return ["确认新增动态输入端口用途", "确认后续连线目标端口正确"]
    if op == "add_node_from_schema":
        return ["确认新增节点类型和页面", "确认新增节点参数需要后续接线或点表复核"]
    return []


def _operation_risk_level(operation: dict[str, Any]) -> str:
    op = operation.get("op")
    if op in {"copy_block", "set_io_point", "delete_node", "delete_block"}:
        return "high"
    if op in {"add_node_from_schema", "connect", "disconnect", "enable_dynamic_input"}:
        return "medium"
    if op == "update_param":
        params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
        if any(_is_risky_field(str(field)) for field in params):
            return "high"
    return "low"


def _compact_changes_for_confirmation(op: str, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for change in changes[:10]:
        if op == "copy_block":
            compacted.append(
                {
                    "block_id": change.get("block_id"),
                    "target_tab_id": change.get("target_tab_id"),
                    "copied_node_count": change.get("copied_node_count"),
                    "dropped_external_input_count": change.get("dropped_external_input_count"),
                    "detached_bacnet_object_count": change.get("detached_bacnet_object_count"),
                    "boundary_connection_count": change.get("boundary_connection_count"),
                    "boundary_preview": change.get("boundary_preview"),
                }
            )
        else:
            compacted.append(change)
    return compacted


def _confirmation_summary_text(risk_level: str, operation_summaries: list[dict[str, Any]], reasons: list[str]) -> str:
    if risk_level == "low":
        return "该计划为低风险，系统可自动应用。"
    high_ops = [item["op"] for item in operation_summaries if item.get("risk_level") == "high"]
    medium_ops = [item["op"] for item in operation_summaries if item.get("risk_level") == "medium"]
    parts = [f"需要确认后再应用，需要人工确认，包含 {len(operation_summaries)} 个操作。"]
    if high_ops:
        parts.append("高风险操作：" + "、".join(high_ops))
    if medium_ops:
        parts.append("中风险操作：" + "、".join(medium_ops))
    if reasons:
        parts.append("主要原因：" + "；".join(reasons[:3]))
    return " ".join(parts)


def _normalize_patch_operations(patch: Any) -> list[dict[str, Any]]:
    if not isinstance(patch, dict):
        return []
    if isinstance(patch.get("operations"), list):
        return [operation for operation in patch["operations"] if isinstance(operation, dict)]
    if isinstance(patch.get("op"), str):
        return [patch]
    return []


def _max_risk(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 0) >= order.get(right, 0) else right


def _is_risky_field(field: str) -> bool:
    normalized = field.casefold()
    tokens = ("io", "address", "addr", "channel", "modbus", "bacnet", "mqtt", "object", "point")
    return any(token in normalized for token in tokens) or any(token in field for token in ("地址", "通道", "点位", "对象"))


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _is_patch_confirmed(state: AgentState, patch: dict[str, Any]) -> bool:
    confirmation = state.get("patch_confirmation")
    if not isinstance(confirmation, dict) or confirmation.get("action") != "approved":
        return False
    if confirmation.get("confirmed_project_version_id") != state.get("current_project_version_id"):
        return False
    confirmed_patch = confirmation.get("confirmed_patch")
    try:
        return json.dumps(confirmed_patch, sort_keys=True, ensure_ascii=False) == json.dumps(patch, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return False


def _matching_planner_result(planner_result: Any, patch: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(planner_result, dict):
        return None
    planned_patch = planner_result.get("pending_patch")
    if planned_patch is None:
        return None
    try:
        if json.dumps(planned_patch, sort_keys=True, ensure_ascii=False) == json.dumps(patch, sort_keys=True, ensure_ascii=False):
            return planner_result
    except TypeError:
        return None
    return None
