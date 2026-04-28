from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.requirement_analysis import analyze_requirement, explain_template_candidate
from app.services.retrieval import get_template_by_id, search_templates


ROOT_DIR = Path(__file__).resolve().parents[1]
EVALS_PATH = ROOT_DIR / "evals" / "template_selection_cases.jsonl"


def test_template_selection_eval_cases_top3_and_clarification() -> None:
    cases = _load_cases()
    assert cases

    evaluable_counts: Counter[str] = Counter()
    top1_hits = 0
    top3_hits = 0
    evaluated_count = 0

    for case in cases:
        summary = analyze_requirement(case["message"], project_type=case.get("project_type"))
        assert bool(summary["blocking_missing_fields"]) is bool(case["expected_clarification"]), case["case_id"]

        if case["expected_clarification"]:
            assert summary["clarification_questions"], case["case_id"]
            continue

        candidates = search_templates(case["message"], project_type=summary["project_type"], limit=3)
        top3_ids = [candidate["template_id"] for candidate in candidates]
        expected_top1 = case["expected_top1"]
        assert expected_top1 is not None, case["case_id"]
        assert top3_ids, case["case_id"]
        assert top3_ids[0] == expected_top1, case["case_id"]
        for expected_id in case["expected_top3"]:
            assert expected_id in top3_ids, case["case_id"]

        evaluated_count += 1
        top1_hits += int(top3_ids[0] == expected_top1)
        top3_hits += int(expected_top1 in top3_ids)
        evaluable_counts[str(summary["project_type"])] += 1

    assert evaluable_counts["ahu"] >= 10
    assert evaluable_counts["plant_room"] >= 10
    assert top1_hits == evaluated_count
    assert top3_hits == evaluated_count


def test_template_candidate_explanation_reports_match_missing_cost_and_risks() -> None:
    summary = analyze_requirement("我要做风冷热泵机房群控程序，包含水泵、旁通阀和 Modbus 通讯。", project_type="plant_room")
    candidate = get_template_by_id("plant_room_efb00c114dcb")

    explanation = explain_template_candidate(candidate, summary)

    assert "项目类型匹配：机房群控程序" in explanation["matched_items"]
    assert "水泵控制" in explanation["matched_items"]
    assert "旁通阀控制" in explanation["matched_items"]
    assert explanation["missing_items"] == []
    assert explanation["estimated_modification_cost"]["level"] == "low"
    assert any("保护/联锁" in item for item in explanation["risk_points"])


def _load_cases() -> list[dict[str, Any]]:
    with EVALS_PATH.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]
