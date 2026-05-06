from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.services.json_project import ROOT_DIR
from app.services.llm_planner import StructuredPatchPlan, plan_patch_with_llm
from app.services.patch_engine import dry_run_patch_to_project


EVALS_PATH = ROOT_DIR / "evals" / "patch_planning_cases.jsonl"


def test_patch_planning_eval_cases_are_valid_and_dry_runnable() -> None:
    cases = load_eval_cases()
    assert len(cases) >= 10

    case_ids: set[str] = set()
    combo_case_count = 0
    for case in cases:
        case_id = require_str(case, "case_id")
        assert case_id not in case_ids
        case_ids.add(case_id)

        template_path = ROOT_DIR / require_str(case, "template_path")
        assert template_path.exists(), case_id

        expected = require_dict(case, "expected")
        plan = StructuredPatchPlan.model_validate(require_dict(case, "reference_plan"))
        assert plan.status == expected["status"], case_id
        assert plan.risk_level == expected["risk_level"], case_id
        assert [operation.op for operation in plan.operations] == expected["ops"], case_id
        if expected["ops"] == ["add_node_from_schema", "enable_dynamic_input", "connect"]:
            combo_case_count += 1

        if plan.status == "needs_clarification":
            assert plan.questions, case_id
            for keyword in expected.get("question_keywords", []):
                assert any(keyword in question for question in plan.questions), case_id
            continue

        patch = {"operations": [operation.model_dump(exclude_none=True) for operation in plan.operations]}
        dry_run = dry_run_patch_to_project(str(template_path), patch)
        assert dry_run["valid"] is expected["dry_run_valid"], case_id
        for key, value in expected["diff_summary"].items():
            assert dry_run["diff"]["summary"][key] == value, case_id

    assert combo_case_count >= 3


@pytest.mark.skipif(os.getenv("RUN_LLM_INTEGRATION") != "1", reason="需要显式开启真实 LLM 集成验收。")
def test_llm_planner_real_deepseek_returns_structured_dry_runnable_plan(tmp_path: Path) -> None:
    del tmp_path
    case = next(item for item in load_eval_cases() if item["case_id"] == "plant_update_pump_threshold")
    template_path = ROOT_DIR / require_str(case, "template_path")

    result = plan_patch_with_llm(
        "请把节点 3a4c97e 的 tripPoint 改为 3。目标节点 id 已明确；如果可以规划，请只输出结构化补丁计划。",
        project_path=str(template_path),
        template_id=require_str(case, "template_id"),
        project_type=require_str(case, "project_type"),
        provider="deepseek",
        max_block_contexts=1,
    )

    assert result["planner"] == "llm"
    assert result["status"] == "planned"
    assert result["pending_patch"]
    plan = StructuredPatchPlan.model_validate(
        {
            key: result[key]
            for key in [
                "status",
                "intent",
                "summary",
                "risk_level",
                "risk_reasons",
                "required_context",
                "operations",
                "validation_expectations",
                "questions",
            ]
        }
    )
    assert plan.operations
    assert all(operation.op in {"update_param", "replace_constant", "enable_dynamic_input", "set_io_point", "rename_node", "add_tab", "add_comment", "add_node_from_schema", "copy_block", "connect", "disconnect"} for operation in plan.operations)

    dry_run = dry_run_patch_to_project(str(template_path), result["pending_patch"])
    assert dry_run["valid"]
    assert dry_run["diff"]["summary"]["affected_node_count"] >= 1


def load_eval_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(EVALS_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        data = json.loads(line)
        assert isinstance(data, dict), line_number
        cases.append(data)
    return cases


def require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    assert isinstance(value, str) and value, key
    return value


def require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    assert isinstance(value, dict), key
    return value
