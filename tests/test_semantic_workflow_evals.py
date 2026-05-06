from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.graph.state import initial_state
from app.graph.workflow import invoke_workflow, invoke_workflow_resume
from app.services.json_project import ROOT_DIR, load_project
from app.services.llm_planner import StructuredPatchPlan
from app.services.patch_engine import dry_run_patch_to_project


SEMANTIC_EVALS_PATH = ROOT_DIR / "evals" / "semantic_workflow_cases.jsonl"
PATCH_EVALS_PATH = ROOT_DIR / "evals" / "patch_planning_cases.jsonl"


def test_semantic_workflow_eval_cases(tmp_path: Path) -> None:
    cases = _load_jsonl(SEMANTIC_EVALS_PATH)
    assert len(cases) >= 8
    assert {case["kind"] for case in cases} >= {"workflow_patch", "workflow_clarification", "reference_patch_plan"}

    patch_cases = {case["case_id"]: case for case in _load_jsonl(PATCH_EVALS_PATH)}
    workflow_case_count = 0
    reference_case_count = 0

    for index, case in enumerate(cases):
        if case["kind"] == "reference_patch_plan":
            reference_case_count += 1
            _assert_reference_patch_plan(case, patch_cases)
            continue

        workflow_case_count += 1
        thread_id = f"semantic-eval-{index}-{case['case_id']}"
        result = _run_workflow_case(case, versions_dir=tmp_path / case["case_id"], thread_id=thread_id)
        assert result["status"] == case["expected_status"], case["case_id"]

        if case["kind"] == "workflow_clarification":
            _assert_workflow_clarification(case, result)
            continue

        _assert_workflow_patch(case, result, thread_id=thread_id)

    assert workflow_case_count >= 5
    assert reference_case_count >= 3


def _run_workflow_case(case: dict[str, Any], *, versions_dir: Path, thread_id: str) -> dict[str, Any]:
    state = initial_state(
        require_str(case, "initial_message"),
        project_type=require_str(case, "project_type"),
        auto_confirm_template=True,
    )
    state["versions_dir"] = str(versions_dir)
    result = invoke_workflow(state, thread_id=thread_id)
    assert result["status"] == "project_version_ready", case["case_id"]

    for message in case["messages"]:
        result["messages"] = list(result["messages"]) + [{"role": "user", "content": str(message)}]
        result = invoke_workflow(result, thread_id=thread_id)
    return result


def _assert_workflow_clarification(case: dict[str, Any], result: dict[str, Any]) -> None:
    assert result["next_action"] == "clarify_patch", case["case_id"]
    candidates = result.get("semantic_target_candidates") or []
    assert len(candidates) >= int(case.get("expected_candidate_count_min", 1)), case["case_id"]
    assert all(candidate.get("display_name") for candidate in candidates), case["case_id"]
    question_text = _question_text(result)
    for expected in case.get("expected_question_contains", []):
        assert expected in question_text, case["case_id"]
    if case.get("expected_no_node_id_question"):
        assert "节点 id" not in question_text
        assert "node id" not in question_text.lower()


def _assert_workflow_patch(case: dict[str, Any], result: dict[str, Any], *, thread_id: str) -> None:
    if case.get("resume_action"):
        assert result["next_action"] == "confirm_patch", case["case_id"]
        result = invoke_workflow_resume({"action": case["resume_action"]}, thread_id=thread_id)
        assert result["status"] == case["expected_after_resume_status"], case["case_id"]

    if case.get("expected_patch_op"):
        pending_patch = result.get("planner_result", {}).get("pending_patch") or result.get("patch_result", {}).get("patch") or {}
        operations = pending_patch.get("operations") if isinstance(pending_patch, dict) else None
        first_op = operations[0] if isinstance(operations, list) and operations else pending_patch
        assert isinstance(first_op, dict)
        assert first_op.get("op") == case["expected_patch_op"], case["case_id"]

    expected_target_id = case.get("expected_target_id")
    if expected_target_id:
        assert expected_target_id in result.get("last_affected_node_ids", []), case["case_id"]
    if case.get("expected_reference") == "last_affected_node_ids":
        assert result.get("planner_result", {}).get("target_resolution", {}).get("selected", {}).get("selector") == {"id": expected_target_id}
    if case.get("expected_selected_candidate_id"):
        assert result.get("planner_result", {}).get("target_resolution", {}).get("selection", {}).get("candidate_id") == case["expected_selected_candidate_id"]

    _assert_node_fields(case, result)
    if case.get("expected_no_node_id_question"):
        assert "节点 id" not in _question_text(result)


def _assert_node_fields(case: dict[str, Any], result: dict[str, Any]) -> None:
    expected_fields = case.get("expected_node_fields")
    if not isinstance(expected_fields, dict) or not expected_fields:
        return
    affected_ids = result.get("last_affected_node_ids") or []
    nodes = load_project(result["current_project_path"])
    candidates = [node for node in nodes if node.get("id") in affected_ids]
    if case.get("expected_target_id"):
        candidates = [node for node in nodes if node.get("id") == case["expected_target_id"]]
    assert candidates, case["case_id"]
    assert any(all(node.get(field) == value for field, value in expected_fields.items()) for node in candidates), case["case_id"]


def _assert_reference_patch_plan(case: dict[str, Any], patch_cases: dict[str, dict[str, Any]]) -> None:
    source_case = patch_cases[require_str(case, "source_case_id")]
    expected = require_dict(source_case, "expected")
    plan = StructuredPatchPlan.model_validate(require_dict(source_case, "reference_plan"))
    assert plan.status == expected["status"], case["case_id"]
    assert plan.risk_level == expected["risk_level"], case["case_id"]
    assert [operation.op for operation in plan.operations] == expected["ops"], case["case_id"]

    template_path = ROOT_DIR / require_str(source_case, "template_path")
    patch = {"operations": [operation.model_dump(exclude_none=True) for operation in plan.operations]}
    dry_run = dry_run_patch_to_project(str(template_path), patch)
    assert dry_run["valid"] is expected["dry_run_valid"], case["case_id"]
    for key, value in expected["diff_summary"].items():
        assert dry_run["diff"]["summary"][key] == value, case["case_id"]


def _question_text(result: dict[str, Any]) -> str:
    planner = result.get("planner_result") if isinstance(result.get("planner_result"), dict) else {}
    questions = planner.get("questions") if isinstance(planner, dict) else []
    messages = result.get("messages") if isinstance(result.get("messages"), list) else []
    return json.dumps({"questions": questions, "messages": messages[-3:]}, ensure_ascii=False)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    assert isinstance(value, str) and value, key
    return value


def require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    assert isinstance(value, dict), key
    return value
