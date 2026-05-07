from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.requirement_analysis import analyze_requirement
from app.services.requirement_model import merge_requirement_slots, requirement_summary_from_slots


ROOT_DIR = Path(__file__).resolve().parents[1]
EVALS_PATH = ROOT_DIR / "evals" / "requirement_extraction_cases.jsonl"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def test_requirement_extraction_eval_cases_cover_deadline_business_scope() -> None:
    cases = _load_cases()
    assert len(cases) >= 30

    project_counts = Counter(case.get("expected_project_type") for case in cases)
    assert project_counts["ahu"] >= 15
    assert project_counts["plant_room"] >= 15
    assert any(len(case.get("messages") or []) > 1 for case in cases)
    assert sum(1 for case in cases if case.get("expected_risk_level") == "high") >= 6

    for case in cases:
        slots, summary = _evaluate_case(case)
        searchable = _searchable_text(slots, summary)

        assert summary["project_type"] == case.get("expected_project_type"), case["case_id"]
        assert summary["ready_for_template_search"] is case["expected_ready"], case["case_id"]
        for field in case.get("expected_missing", []):
            assert field in summary["missing_fields"], case["case_id"]
        for expected in case.get("expected_contains", []):
            assert expected in searchable, case["case_id"]
        for absent in case.get("expected_absent", []):
            assert absent not in searchable, case["case_id"]

        expected_risk = case.get("expected_risk_level")
        if expected_risk:
            assert RISK_ORDER[summary["risk_level"]] >= RISK_ORDER[expected_risk], case["case_id"]


def _evaluate_case(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    slots: dict[str, Any] | None = None
    rule_summary: dict[str, Any] | None = None
    for message in case.get("messages") or [case["message"]]:
        rule_summary = analyze_requirement(
            message,
            existing=rule_summary,
            project_type=case.get("project_type"),
        )
        slots = merge_requirement_slots(
            message=message,
            rule_summary=rule_summary,
            existing_slots=slots,
        )
        rule_summary = requirement_summary_from_slots(slots)
    assert slots is not None
    return slots, requirement_summary_from_slots(slots)


def _searchable_text(slots: dict[str, Any], summary: dict[str, Any]) -> str:
    status_items = [f"{key}={value}" for key, value in sorted((slots.get("slot_status") or {}).items())]
    parts: list[str] = [
        str(summary.get("project_type")),
        str(summary.get("task_type")),
        str(summary.get("risk_level")),
        *(summary.get("equipment") or []),
        *(summary.get("control_features") or []),
        *(summary.get("communication") or []),
        *(summary.get("io_points") or []),
        *(summary.get("protection_logic") or []),
        *(summary.get("design_constraints") or []),
        *(summary.get("missing_fields") or []),
        *(summary.get("confirmed_requirements") or []),
        *(summary.get("risk_items") or []),
        *status_items,
    ]
    return "\n".join(parts)


def _load_cases() -> list[dict[str, Any]]:
    with EVALS_PATH.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]
