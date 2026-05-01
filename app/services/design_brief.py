from __future__ import annotations

from typing import Any

from app.services.requirement_analysis import explain_template_candidate
from app.services.retrieval import search_nodes


def build_design_brief(
    *,
    requirement_slots: dict[str, Any] | None,
    requirement_summary: dict[str, Any],
    template_candidates: list[dict[str, Any]],
    selected_template_id: str | None = None,
) -> dict[str, Any] | None:
    """生成轻量设计摘要，用于模板确认前后的工程师审阅。"""

    if not template_candidates:
        return None

    selected = _selected_candidate(template_candidates, selected_template_id)
    coverage = [_candidate_coverage(candidate, requirement_summary) for candidate in template_candidates]
    selected_coverage = next(item for item in coverage if item["template_id"] == selected["template_id"])
    plans = _requirement_plans(requirement_summary)
    evidence = _template_evidence(selected, plans["all_items"])
    satisfied = _unique([*selected_coverage["matched_items"], *evidence["covered"]])
    modification_items = _unique([*selected_coverage["missing_items"], *evidence["missing"]])
    clarification_items = _clarification_items(requirement_summary)
    risk_items = _unique([*(requirement_summary.get("risk_items") or []), *selected_coverage["risk_points"]])
    export_gate = _export_gate(clarification_items, risk_items, modification_items)

    return {
        "selected_template": {
            "template_id": selected.get("template_id"),
            "file_name": selected.get("file_name"),
            "project_type": selected.get("project_type"),
            "project_type_label": selected.get("project_type_label"),
            "score": selected.get("score"),
            "tabs": selected.get("tabs", []),
        },
        "recommendation_reasons": _recommendation_reasons(selected, selected_coverage, requirement_summary),
        "satisfied_requirements": satisfied,
        "modification_items": modification_items,
        "clarification_items": clarification_items,
        "risk_items": risk_items,
        "equipment_plan": plans["equipment_plan"],
        "control_plan": plans["control_plan"],
        "point_plan": plans["point_plan"],
        "protection_plan": plans["protection_plan"],
        "export_gate": export_gate,
        "template_coverage": coverage,
        "requirement_source": _requirement_source(requirement_slots, requirement_summary),
        "summary": _brief_summary(selected, plans, modification_items, risk_items),
    }


def selected_design_brief_for_state(state: dict[str, Any]) -> dict[str, Any] | None:
    return build_design_brief(
        requirement_slots=state.get("requirement_slots"),
        requirement_summary=state.get("requirement_summary") or {},
        template_candidates=state.get("template_candidates") or [],
        selected_template_id=state.get("selected_template_id"),
    )


def _selected_candidate(template_candidates: list[dict[str, Any]], selected_template_id: str | None) -> dict[str, Any]:
    if selected_template_id:
        for candidate in template_candidates:
            if candidate.get("template_id") == selected_template_id:
                return candidate
    return template_candidates[0]


def _candidate_coverage(candidate: dict[str, Any], requirement_summary: dict[str, Any]) -> dict[str, Any]:
    explanation = {
        "matched_items": list(candidate.get("matched_items") or []),
        "missing_items": list(candidate.get("missing_items") or []),
        "estimated_modification_cost": candidate.get("estimated_modification_cost") or {},
        "risk_points": list(candidate.get("risk_points") or []),
    }
    if not explanation["matched_items"] and not explanation["missing_items"]:
        explanation = explain_template_candidate(candidate, requirement_summary)
    return {
        "template_id": candidate.get("template_id"),
        "file_name": candidate.get("file_name"),
        "score": candidate.get("score"),
        "matched_items": explanation["matched_items"],
        "missing_items": explanation["missing_items"],
        "estimated_modification_cost": explanation["estimated_modification_cost"],
        "risk_points": explanation["risk_points"],
        "recommendation_reasons": _candidate_reasons(candidate, explanation),
    }


def _candidate_reasons(candidate: dict[str, Any], explanation: dict[str, Any]) -> list[str]:
    reasons = [str(item) for item in candidate.get("reasons") or [] if str(item)]
    if explanation["matched_items"]:
        reasons.append("已覆盖：" + "、".join(explanation["matched_items"][:5]))
    cost = explanation.get("estimated_modification_cost") or {}
    cost_reasons = [str(item) for item in cost.get("reasons") or [] if str(item)]
    reasons.extend(cost_reasons[:2])
    return _unique(reasons)


def _requirement_plans(requirement_summary: dict[str, Any]) -> dict[str, list[str]]:
    equipment = [str(item) for item in requirement_summary.get("equipment") or [] if str(item)]
    control = [str(item) for item in requirement_summary.get("control_features") or [] if str(item)]
    communication = [str(item) for item in requirement_summary.get("communication") or [] if str(item)]
    io_points = [str(item) for item in requirement_summary.get("io_points") or [] if str(item)]
    protection = [str(item) for item in requirement_summary.get("protection_logic") or [] if str(item)]
    return {
        "equipment_plan": equipment,
        "control_plan": control,
        "point_plan": _unique([*communication, *io_points]),
        "protection_plan": protection,
        "all_items": _unique([*equipment, *control, *communication, *io_points, *protection]),
    }


def _template_evidence(candidate: dict[str, Any], requirement_items: list[str]) -> dict[str, list[str]]:
    covered: list[str] = []
    missing: list[str] = []
    features = candidate.get("features") if isinstance(candidate.get("features"), dict) else {}
    for item in requirement_items:
        feature_label = _feature_coverage_label(item, features)
        if feature_label:
            covered.append(feature_label)
            continue
        node_hits = search_nodes(item, template_id=str(candidate["template_id"]), limit=1)
        if node_hits:
            node = node_hits[0]
            node_label = str(node.get("name") or node.get("label") or node.get("node_id") or item)
            tab_label = str(node.get("tab_label") or "未知页面")
            covered.append(f"{item}：模板节点命中 {tab_label}/{node_label}")
        else:
            missing.append(f"{item}：需要后续补丁或人工确认")
    return {"covered": covered, "missing": missing}


def _feature_coverage_label(item: str, features: dict[str, Any]) -> str | None:
    checks = [
        ("水泵", "has_pump_control", "水泵控制"),
        ("泵", "has_pump_control", "水泵控制"),
        ("旁通", "has_bypass_valve", "旁通阀控制"),
        ("冷却塔", "has_cooling_tower", "冷却塔控制"),
        ("风冷热泵", "has_air_source_heat_pump", "风冷热泵控制"),
        ("热泵", "has_air_source_heat_pump", "风冷热泵控制"),
        ("阀", "has_valve_control", "阀门控制"),
        ("排风", "has_exhaust_fan", "排风机"),
        ("直膨", "has_dx_unit", "直膨机"),
        ("Modbus", "has_modbus", "Modbus 通讯"),
        ("BACnet", "has_bacnet", "BACnet 通讯"),
        ("MQTT", "has_mqtt", "MQTT 通讯"),
        ("硬接", "has_hard_io", "硬接 IO"),
        ("IO", "has_hard_io", "硬接 IO"),
    ]
    for token, feature, label in checks:
        if token in item and features.get(feature):
            return f"{item}：模板已包含{label}"
    return None


def _clarification_items(requirement_summary: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for question in requirement_summary.get("open_questions") or []:
        if isinstance(question, dict) and question.get("question"):
            items.append(str(question["question"]))
    return _unique(items)


def _export_gate(clarification_items: list[str], risk_items: list[str], modification_items: list[str]) -> list[str]:
    gate = ["导出前必须通过工程结构校验。"]
    if modification_items:
        gate.append("需改造项完成后再做需求覆盖复核。")
    if clarification_items:
        gate.append("未确认项需在导出前确认或标记为人工复核。")
    if risk_items:
        gate.append("中高风险修改必须 dry-run 通过并经人工确认。")
    return gate


def _recommendation_reasons(
    candidate: dict[str, Any],
    coverage: dict[str, Any],
    requirement_summary: dict[str, Any],
) -> list[str]:
    reasons = list(coverage.get("recommendation_reasons") or [])
    project_type = requirement_summary.get("project_type")
    tabs = [str(item) for item in candidate.get("tabs") or []]
    request_text = " ".join(
        str(item)
        for item in [
            *(requirement_summary.get("equipment") or []),
            *(requirement_summary.get("control_features") or []),
            *(requirement_summary.get("communication") or []),
        ]
    )
    if project_type == "ahu" and any("排风机" in tab for tab in tabs) and any(token in request_text for token in ("排风机", "直膨", "除湿")):
        reasons.append("包含独立排风机/直膨机故障页面，适合带排风机或直膨扩展的 AHU。")
    if project_type == "ahu" and len(tabs) == 4:
        reasons.append("四页结构适合标准 IO/通讯、控制、定时和直膨状态需求。")
    if project_type == "plant_room" and any("风冷热泵" in tab for tab in tabs) and any(token in request_text for token in ("风冷热泵", "旁通", "Modbus", "硬接")):
        reasons.append("风冷热泵、旁通阀、硬接/通讯页面齐全，适合风冷热泵群控。")
    if project_type == "plant_room" and any("冷却塔" in tab for tab in tabs) and any(token in request_text for token in ("主机", "冷冻泵", "冷却泵", "冷却塔", "加减载", "轮值")):
        reasons.append("主机、冷冻/冷却泵、冷却塔、加减载和轮询页面齐全，适合水冷群控。")
    return _unique(reasons)


def _requirement_source(requirement_slots: dict[str, Any] | None, requirement_summary: dict[str, Any]) -> dict[str, Any]:
    slots = requirement_slots or {}
    return {
        "source": slots.get("source") or "rule",
        "message_count": len(slots.get("message_history") or requirement_summary.get("raw_requirements") or []),
        "risk_level": requirement_summary.get("risk_level"),
    }


def _brief_summary(
    selected: dict[str, Any],
    plans: dict[str, list[str]],
    modification_items: list[str],
    risk_items: list[str],
) -> str:
    parts = [f"推荐模板：{selected.get('file_name') or selected.get('template_id')}"]
    if plans["equipment_plan"]:
        parts.append("设备：" + "、".join(plans["equipment_plan"][:6]))
    if plans["control_plan"]:
        parts.append("控制：" + "、".join(plans["control_plan"][:6]))
    if modification_items:
        parts.append(f"需改造 {len(modification_items)} 项")
    if risk_items:
        parts.append(f"风险 {len(risk_items)} 项")
    return "；".join(parts)


def _unique(items: list[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result
