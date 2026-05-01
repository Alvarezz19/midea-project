from __future__ import annotations

from typing import Any

import pytest

from app.graph.state import initial_state
from app.graph.workflow import invoke_workflow
from app.services.requirement_analysis import analyze_requirement
from app.services.requirement_model import merge_requirement_slots, requirement_summary_from_slots


@pytest.mark.parametrize(
    ("message", "project_type", "expected"),
    [
        ("我要做 AHU，带直膨、排风机、CO2、新风阀、Modbus", "ahu", ["直膨机", "排风机", "新风阀", "CO2 控制", "Modbus"]),
        ("我要做一个控制程序", None, ["project_type"]),
        ("我要做 AHU", "ahu", ["equipment_or_control_goal"]),
        ("风冷热泵机房，两台水泵，旁通阀压差控制，Modbus", "plant_room", ["水泵 2 台", "旁通阀", "压差控制"]),
        ("水冷机房 2 台主机 3 台冷冻泵 3 台冷却泵 2 台冷却塔，加减载轮询", "plant_room", ["主机 2 台", "冷冻泵 3 台", "冷却泵 3 台", "冷却塔 2 台", "加减载", "轮值"]),
        ("AHU 需要过滤网报警、防冻保护和故障反馈", "ahu", ["过滤网报警", "防冻保护", "故障报警", "运行反馈"]),
        ("AHU 送风温度 PID 控制，硬接 IO", "ahu", ["温度控制", "PID 控制", "硬接 IO"]),
        ("机房群控修改 Modbus 地址", "plant_room", ["high"]),
        ("AHU 删除防冻保护", "ahu", ["high"]),
        ("AHU 需要回风阀、水阀、电加热和除湿", "ahu", ["回风阀", "水阀", "电加热", "除湿"]),
    ],
)
def test_requirement_slots_extract_core_cases(message: str, project_type: str | None, expected: list[str]) -> None:
    slots = merge_requirement_slots(
        message=message,
        rule_summary=analyze_requirement(message, project_type=project_type),
        existing_slots=None,
    )
    summary = requirement_summary_from_slots(slots)
    searchable = " ".join(
        [
            *summary.get("equipment", []),
            *summary.get("control_features", []),
            *summary.get("communication", []),
            *summary.get("io_points", []),
            *summary.get("protection_logic", []),
            *summary.get("missing_fields", []),
            str(summary.get("risk_level")),
        ]
    )

    for item in expected:
        assert item in searchable


def test_requirement_slots_override_equipment_count() -> None:
    first = merge_requirement_slots(
        message="机房群控需要 2 台水泵，Modbus 通讯",
        rule_summary=analyze_requirement("机房群控需要 2 台水泵，Modbus 通讯", project_type="plant_room"),
        existing_slots=None,
    )
    second = merge_requirement_slots(
        message="水泵改成 3 台",
        rule_summary=analyze_requirement("水泵改成 3 台", project_type="plant_room"),
        existing_slots=first,
    )

    summary = requirement_summary_from_slots(second)

    assert "水泵 3 台" in summary["equipment"]
    assert "水泵 2 台" not in summary["equipment"]
    assert second["slot_status"]["equipment:水泵"] == "confirmed"
    assert second["risk_level"] == "high"
    assert any("设备数量变化" in item for item in second["risk_items"])


def test_requirement_slots_rejects_previous_equipment() -> None:
    first = merge_requirement_slots(
        message="我要做 AHU，需要排风机、直膨机和 Modbus",
        rule_summary=analyze_requirement("我要做 AHU，需要排风机、直膨机和 Modbus", project_type="ahu"),
        existing_slots=None,
    )
    second = merge_requirement_slots(
        message="不要排风机",
        rule_summary=analyze_requirement("不要排风机", project_type="ahu"),
        existing_slots=first,
    )
    summary = requirement_summary_from_slots(second)

    assert "排风机" not in summary["equipment"]
    assert "直膨机" in summary["equipment"]
    assert second["slot_status"]["equipment:排风机"] == "rejected"


def test_collect_requirements_merges_llm_slots(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def fake_extract(message: str, *, provider: str | None = None) -> dict[str, Any]:
        return {
            "project_type": "ahu",
            "project_type_confidence": 0.9,
            "equipment": ["排风机"],
            "control_features": ["CO2 控制"],
            "communication": ["Modbus"],
            "io_points": ["CO2"],
            "protection_logic": ["故障报警"],
            "missing_fields": [],
            "clarification_questions": [],
            "risk_level": "low",
            "risk_reasons": [],
            "llm_meta": {"provider": provider or "deepseek"},
        }

    monkeypatch.setattr("app.graph.nodes.extract_requirement_with_llm", fake_extract)
    state = initial_state("我要做 AHU，带 CO2", project_type="ahu", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)

    result = invoke_workflow(state)

    assert result["requirement_slots"]["source"] == "merged"
    assert "排风机" in result["requirement_summary"]["equipment"]
    assert "CO2 控制" in result["requirement_summary"]["control_features"]
    assert "Modbus" in result["requirement_summary"]["communication"]
    assert result["status"] == "project_version_ready"


def test_existing_project_message_routes_to_patch_planning(tmp_path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序，包含水泵和旁通阀控制", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"

    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把节点 67febfa 的常量值改为 6"}]
    result = invoke_workflow(created)

    assert result["status"] in {"patch_applied", "awaiting_patch_clarification"}
    assert result["requirement_summary"]["task_type"] == "modify_existing"
    assert result["next_action"] != "clarify_requirements"
