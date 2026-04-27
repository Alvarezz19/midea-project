from __future__ import annotations

from pathlib import Path
from typing import Any

from app.graph.state import AgentState
from app.services.json_project import create_project_version, load_project, save_project
from app.services.patch_engine import PatchEngineError, apply_patch
from app.services.planner import plan_patch_request
from app.services.retrieval import RetrievalError, get_template_by_id, normalize_project_type, search_templates
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
    existing = dict(state.get("requirement_summary") or {})
    raw_requirements = list(existing.get("raw_requirements", []))
    if user_text and user_text not in raw_requirements:
        raw_requirements.append(user_text)

    summary = {
        **existing,
        "raw_requirements": raw_requirements,
        "latest_user_message": user_text,
        "project_type": state.get("project_type"),
    }
    return {"requirement_summary": summary, "status": "requirements_collected"}


def retrieve_template_candidates(state: AgentState) -> dict[str, Any]:
    project_type = state.get("project_type")
    if not project_type:
        return {"template_candidates": [], "status": "need_project_type", "next_action": "ask_project_type"}

    query = _requirement_query(state)
    candidates = search_templates(query, project_type=project_type, limit=3)
    if not candidates:
        return {"template_candidates": [], "status": "no_template_candidate", "next_action": "ask_more_requirements"}
    slim_candidates = [_slim_template_candidate(candidate) for candidate in candidates]
    return {
        "template_candidates": slim_candidates,
        "status": "template_candidates_ready",
        "next_action": "confirm_template",
    }


def select_or_wait_template(state: AgentState) -> dict[str, Any]:
    selected_template_id = state.get("selected_template_id")
    candidates = state.get("template_candidates") or []
    if state.get("status") in {"need_project_type", "no_template_candidate"}:
        return {"status": state.get("status"), "next_action": state.get("next_action")}

    if selected_template_id:
        return {"selected_template_id": selected_template_id, "status": "template_selected", "next_action": None}

    if state.get("auto_confirm_template") and candidates:
        return {
            "selected_template_id": candidates[0]["template_id"],
            "status": "template_selected",
            "next_action": None,
        }

    return {
        "status": "awaiting_template_confirmation",
        "next_action": "confirm_template",
    }


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

    result = plan_patch_request(
        _last_user_content(state),
        project_path=project_path,
        template_id=state.get("selected_template_id"),
        project_type=state.get("project_type"),
    )
    if result.get("status") == "planned":
        return {
            "planner_result": result,
            "pending_patch": result.get("pending_patch"),
            "status": "patch_planned",
            "next_action": None,
        }
    return {
        "planner_result": result,
        "status": "awaiting_patch_clarification",
        "next_action": "clarify_patch",
    }


def apply_pending_patch_node(state: AgentState) -> dict[str, Any]:
    pending_patch = state.get("pending_patch")
    project_path = state.get("current_project_path")
    if not pending_patch:
        return {"status": "ready_for_user_patch", "next_action": "wait_user_patch"}
    if not project_path:
        return {"status": "error", "error": "存在 pending_patch，但 current_project_path 为空。", "next_action": "fix_project_version"}

    try:
        nodes = load_project(project_path)
        result = apply_patch(nodes, pending_patch)
        report = validate_project(result["nodes"])
        if report["valid"]:
            save_project(project_path, result["nodes"])
    except (PatchEngineError, ValueError) as exc:
        return {"status": "patch_failed", "error": str(exc), "next_action": "revise_patch"}

    return {
        "patch_result": {"changed": result["changed"], "changes": result["changes"]},
        "validation_report": report,
        "pending_patch": None,
        "status": "patch_applied" if report["valid"] else "validation_failed",
        "next_action": None if report["valid"] else "revise_patch",
    }


def validate_current_project_node(state: AgentState) -> dict[str, Any]:
    project_path = state.get("current_project_path")
    if not project_path:
        return {"status": state.get("status", "no_project_version"), "next_action": state.get("next_action")}

    try:
        report = validate_project(load_project(project_path))
    except ValueError as exc:
        return {"status": "validation_failed", "error": str(exc), "next_action": "fix_project_json"}
    return {
        "validation_report": report,
        "status": state.get("status") if report["valid"] else "validation_failed",
        "next_action": state.get("next_action") if report["valid"] else "fix_project_json",
    }


def summarize_result_node(state: AgentState) -> dict[str, Any]:
    messages = list(state.get("messages") or [])
    summary = _build_assistant_summary(state)
    messages.append({"role": "assistant", "content": summary})
    return {"messages": messages}


def route_after_template_selection(state: AgentState) -> str:
    if state.get("status") == "template_selected":
        return "create_project_version"
    return "summarize_result"


def route_after_requirements(state: AgentState) -> str:
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


def _requirement_query(state: AgentState) -> str:
    summary = state.get("requirement_summary") or {}
    raw = summary.get("raw_requirements")
    if isinstance(raw, list) and raw:
        return "\n".join(str(item) for item in raw)
    return _last_user_content(state)


def _slim_template_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
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
    }


def _build_assistant_summary(state: AgentState) -> str:
    status = state.get("status")
    if status == "need_project_type":
        return "请先确认项目类型：机房群控程序或 AHU 程序。"
    if status == "awaiting_template_confirmation":
        candidates = state.get("template_candidates") or []
        lines = ["已找到候选模板，请确认使用哪一个："]
        for index, candidate in enumerate(candidates, start=1):
            lines.append(f"{index}. {candidate['file_name']}，得分 {candidate['score']}，{candidate['summary']}")
        return "\n".join(lines)
    if status == "project_version_ready":
        return f"已创建工程版本：{state.get('current_project_version_id')}，路径：{state.get('current_project_path')}。"
    if status == "ready_for_user_patch":
        return f"工程版本已就绪，等待用户提出局部修改需求。路径：{state.get('current_project_path')}。"
    if status == "awaiting_patch_clarification":
        planner_result = state.get("planner_result") or {}
        questions = planner_result.get("questions") or ["请补充修改目标。"]
        return "需要补充信息：" + "；".join(str(question) for question in questions)
    if status == "patch_applied":
        patch_result = state.get("patch_result") or {}
        changes = patch_result.get("changes") or []
        return f"补丁已应用并校验通过，共 {len(changes)} 项变更。"
    if status == "validation_failed":
        report = state.get("validation_report") or {}
        return f"工程校验失败：errors={report.get('error_count')}，warnings={report.get('warning_count')}。"
    if status in {"patch_failed", "error"}:
        return f"处理失败：{state.get('error')}"
    return f"当前状态：{status}。"
