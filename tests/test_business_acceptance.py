from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.graph.state import initial_state
from app.graph.workflow import invoke_workflow, invoke_workflow_resume
from app.services.flow_graph import build_react_flow
from app.services.json_project import load_project


ROOT_DIR = Path(__file__).resolve().parents[1]
EVALS_PATH = ROOT_DIR / "evals" / "business_acceptance_cases.jsonl"


def test_business_acceptance_project_design_cases(tmp_path: Path) -> None:
    cases = _load_cases()
    assert len(cases) >= 6

    for index, case in enumerate(cases):
        state = initial_state(
            case["messages"][0],
            project_type=case.get("project_type"),
            auto_confirm_template=bool(case.get("auto_confirm_template")),
        )
        state["versions_dir"] = str(tmp_path / case["case_id"])
        result = invoke_workflow(state, thread_id=f"business-{index}-{case['case_id']}")
        for message in case["messages"][1:]:
            result["messages"] = list(result["messages"]) + [{"role": "user", "content": message}]
            result = invoke_workflow(result, thread_id=f"business-{index}-{case['case_id']}")

        assert result["status"] == case["expected_status"], case["case_id"]
        if case.get("expected_template"):
            assert result["selected_template_id"] == case["expected_template"], case["case_id"]
        assert result["template_candidates"], case["case_id"]

        searchable = _state_searchable_text(result)
        for expected in case.get("expected_contains", []):
            assert expected in searchable, case["case_id"]
        for absent in case.get("expected_absent", []):
            assert absent not in searchable, case["case_id"]
        if case.get("expected_risk_level"):
            assert result["requirement_summary"]["risk_level"] == case["expected_risk_level"], case["case_id"]
        if "expected_conformance_context" in case:
            assert result["conformance_report"]["context_available"] is case["expected_conformance_context"], case["case_id"]
        if case.get("expected_conformance_blocked"):
            assert result["conformance_report"]["valid_for_requirement"] is False, case["case_id"]
            assert result["validation_report"]["exportable"] is False, case["case_id"]


def test_business_acceptance_parameter_patch_diff_and_flow_focus(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序，包含水泵和旁通阀控制", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state, thread_id="business-patch-diff")
    assert created["status"] == "project_version_ready"

    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把节点 67febfa 的常量值替换为 6。"}]
    patched = invoke_workflow(created, thread_id="business-patch-diff")

    assert patched["status"] == "patch_applied"
    assert patched["validation_report"]["valid"] is True
    assert patched["current_project_version_id"] != created["current_project_version_id"]
    diff = patched["patch_result"]["diff"]
    assert diff["summary"]["modified_count"] == 1
    assert diff["affected_node_ids"] == ["67febfa"]

    nodes = load_project(patched["current_project_path"])
    flow = build_react_flow(nodes, focus_node_ids=diff["affected_node_ids"], max_nodes=12)
    assert flow["nodes"][0]["id"] == "67febfa"
    assert flow["budget"]["node_count"] <= 12


def test_business_acceptance_high_risk_io_patch_requires_and_resumes_confirmation(tmp_path: Path) -> None:
    thread_id = "business-high-risk-io"
    state = initial_state("我要做一个风冷热泵机房群控程序，包含水泵和 Modbus", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state, thread_id=thread_id)
    assert created["status"] == "project_version_ready"

    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把节点 22a27d1 的物理输入通道改为扩展模块 1、通道 31。"}]
    created["pending_patch"] = {
        "op": "set_io_point",
        "node_selector": {"id": "22a27d1"},
        "params": {"hwExpander": "1", "hwChannelIndex": "31"},
    }
    awaiting = invoke_workflow(created, thread_id=thread_id)

    assert awaiting["status"] == "awaiting_patch_confirmation"
    assert awaiting["risk_assessment"]["risk_level"] == "high"
    assert awaiting["planner_dry_run"]["saved"] is False
    confirmation_points = awaiting["risk_assessment"]["operation_summaries"][0]["confirmation_points"]
    assert any("点表字段" in point for point in confirmation_points)

    resumed = invoke_workflow_resume({"action": "approve"}, thread_id=thread_id)
    assert resumed["status"] == "patch_applied"
    assert resumed["validation_report"]["valid"] is True
    assert resumed["patch_result"]["diff"]["summary"]["modified_count"] == 1


def _state_searchable_text(state: dict[str, Any]) -> str:
    slot_status = state.get("requirement_slots", {}).get("slot_status") or {}
    status_items = [f"{key}={value}" for key, value in sorted(slot_status.items())]
    payload = {
        "requirement_summary": state.get("requirement_summary"),
        "design_brief": state.get("design_brief"),
        "conformance_report": state.get("conformance_report"),
        "template_candidates": state.get("template_candidates"),
        "slot_status": status_items,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _load_cases() -> list[dict[str, Any]]:
    with EVALS_PATH.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]
