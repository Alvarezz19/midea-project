from __future__ import annotations

from typing import Any

from app.services.retrieval import infer_feature_hits, normalize_project_type


FEATURE_LABELS = {
    "has_pump_control": "水泵控制",
    "has_valve_control": "阀门控制",
    "has_bypass_valve": "旁通阀控制",
    "has_cooling_tower": "冷却塔控制",
    "has_air_source_heat_pump": "风冷热泵",
    "has_ahu": "AHU/空调箱",
    "has_exhaust_fan": "排风机",
    "has_dx_unit": "直膨机",
    "has_modbus": "Modbus 通讯",
    "has_bacnet": "BACnet 通讯",
    "has_mqtt": "MQTT 通讯",
    "has_hard_io": "硬接 IO",
}

EQUIPMENT_KEYWORDS = {
    "风冷热泵": ["风冷热泵", "风冷", "热泵"],
    "主机": ["主机", "冷机", "冷水机组"],
    "水泵": ["水泵", "冷冻泵", "冷却泵", "泵"],
    "冷却塔": ["冷却塔"],
    "旁通阀": ["旁通阀", "旁通"],
    "AHU": ["AHU", "ahu", "空调箱", "空气处理机组", "新风机组"],
    "送风机": ["送风机", "风机"],
    "排风机": ["排风机", "排风"],
    "直膨机": ["直膨机", "直膨"],
    "新风阀": ["新风阀", "新风"],
}

CONTROL_KEYWORDS = {
    "轮值": ["轮值", "轮询", "轮换"],
    "加减载": ["加减载", "加载", "减载"],
    "PID 控制": ["PID", "pid", "比例积分"],
    "压差控制": ["压差", "差压"],
    "温度控制": ["温度", "送风温度", "回风温度"],
    "CO2 控制": ["CO2", "二氧化碳"],
    "联动控制": ["联动", "连锁", "联锁"],
    "定时控制": ["定时", "时段", "日程"],
}

COMMUNICATION_KEYWORDS = {
    "Modbus": ["Modbus", "modbus"],
    "BACnet": ["BACnet", "bacnet", "BACnet/IP", "BACIP"],
    "MQTT": ["MQTT", "mqtt"],
    "硬接 IO": ["硬接", "硬线", "IO", "DI", "DO", "AI", "AO"],
}

PROTECTION_KEYWORDS = {
    "防冻保护": ["防冻"],
    "故障报警": ["故障", "报警"],
    "运行反馈": ["反馈", "运行反馈"],
    "手自动": ["手自动", "手/自动"],
    "最小启停延时": ["最小启停", "启停延时", "延时"],
    "过滤网报警": ["过滤网"],
}


def analyze_requirement(message: str, *, existing: dict[str, Any] | None = None, project_type: str | None = None) -> dict[str, Any]:
    """确定性抽取和合并需求摘要，用于模板选择前的轻量澄清。"""

    existing = dict(existing or {})
    raw_requirements = list(existing.get("raw_requirements") or [])
    if message and message not in raw_requirements:
        raw_requirements.append(message)
    combined_text = "\n".join(str(item) for item in raw_requirements)

    normalized_project_type = project_type or existing.get("project_type") or normalize_project_type(combined_text)
    equipment = _merge_values(existing.get("equipment"), _extract_keywords(combined_text, EQUIPMENT_KEYWORDS))
    control_features = _merge_values(existing.get("control_features"), _extract_keywords(combined_text, CONTROL_KEYWORDS))
    communication = _merge_values(existing.get("communication"), _extract_keywords(combined_text, COMMUNICATION_KEYWORDS))
    protection_logic = _merge_values(existing.get("protection_logic"), _extract_keywords(combined_text, PROTECTION_KEYWORDS))
    requested_features = sorted(infer_feature_hits(combined_text))

    missing_fields: list[str] = []
    blocking_missing_fields: list[str] = []
    questions: list[str] = []

    if not normalized_project_type:
        missing_fields.append("project_type")
        blocking_missing_fields.append("project_type")
        questions.append("请先确认项目类型：机房群控程序还是 AHU 程序？")
    if normalized_project_type and not equipment and not control_features:
        missing_fields.append("equipment_or_control_goal")
        blocking_missing_fields.append("equipment_or_control_goal")
        questions.append("请补充主要设备或控制目标，例如水泵、旁通阀、直膨机、排风机或 CO2 控制。")
    if normalized_project_type and not communication:
        missing_fields.append("communication")
        questions.append("请确认通讯或 IO 方式，例如 Modbus、BACnet、MQTT 或硬接 IO。")
    if normalized_project_type and not protection_logic:
        missing_fields.append("protection_logic")
        questions.append("请确认关键保护/联锁要求，例如防冻、故障报警、运行反馈或最小启停延时。")
    if normalized_project_type and not _mentions_count(combined_text):
        missing_fields.append("device_count")
        questions.append("请确认关键设备数量，例如主机、水泵、冷却塔、风机或直膨机数量。")

    confirmed_requirements = _confirmed_requirements(
        project_type=normalized_project_type,
        equipment=equipment,
        control_features=control_features,
        communication=communication,
        protection_logic=protection_logic,
    )
    open_questions = [{"field": field, "question": question} for field, question in zip(missing_fields, questions)]

    return {
        **existing,
        "raw_requirements": raw_requirements,
        "latest_user_message": message,
        "project_type": normalized_project_type,
        "equipment": equipment,
        "control_features": control_features,
        "communication": communication,
        "protection_logic": protection_logic,
        "requested_features": requested_features,
        "missing_fields": missing_fields,
        "blocking_missing_fields": blocking_missing_fields,
        "clarification_questions": questions,
        "open_questions": open_questions,
        "confirmed_requirements": confirmed_requirements,
        "ready_for_template_search": not blocking_missing_fields,
        "summary": _summary(normalized_project_type, equipment, control_features, communication),
    }


def explain_template_candidate(candidate: dict[str, Any], requirement_summary: dict[str, Any]) -> dict[str, Any]:
    """为模板候选生成匹配项、缺失项、预计改造成本和风险点。"""

    features = candidate.get("features") if isinstance(candidate.get("features"), dict) else {}
    requested_features = requirement_summary.get("requested_features")
    if not isinstance(requested_features, list):
        requested_features = []
    matched = [FEATURE_LABELS.get(feature, feature) for feature in requested_features if features.get(feature)]
    missing = [FEATURE_LABELS.get(feature, feature) for feature in requested_features if not features.get(feature)]

    if candidate.get("project_type") == requirement_summary.get("project_type"):
        matched.insert(0, f"项目类型匹配：{candidate.get('project_type_label') or candidate.get('project_type')}")

    cost_level = "low"
    cost_reasons: list[str] = []
    if missing:
        cost_level = "medium" if len(missing) <= 2 else "high"
        cost_reasons.append("需要补齐：" + "、".join(missing[:5]))
    if not matched:
        cost_level = "high"
        cost_reasons.append("未命中明确功能特征。")

    risk_points: list[str] = []
    if missing:
        risk_points.append("缺失功能需要后续结构化补丁改造。")
    missing_fields = requirement_summary.get("missing_fields")
    if isinstance(missing_fields, list):
        if "communication" in missing_fields:
            risk_points.append("通讯/IO 方式未确认，导出前需要复核点表。")
        if "protection_logic" in missing_fields:
            risk_points.append("保护/联锁要求未确认，后续应补规则校验。")
        if "device_count" in missing_fields:
            risk_points.append("设备数量未确认，涉及数量变化时必须人工确认。")

    return {
        "matched_items": matched,
        "missing_items": missing,
        "estimated_modification_cost": {
            "level": cost_level,
            "reasons": cost_reasons or ["当前模板与已知需求匹配度较高。"],
        },
        "risk_points": risk_points,
    }


def _extract_keywords(text: str, keyword_map: dict[str, list[str]]) -> list[str]:
    return [label for label, keywords in keyword_map.items() if any(keyword in text for keyword in keywords)]


def _merge_values(existing: Any, extracted: list[str]) -> list[str]:
    result: list[str] = []
    for value in list(existing or []) + extracted:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return result


def _mentions_count(text: str) -> bool:
    count_tokens = ("台", "个", "路", "点", "2", "3", "4", "两", "三", "四", "数量")
    return any(token in text for token in count_tokens)


def _confirmed_requirements(
    *,
    project_type: str | None,
    equipment: list[str],
    control_features: list[str],
    communication: list[str],
    protection_logic: list[str],
) -> list[str]:
    result: list[str] = []
    if project_type:
        label = "机房群控程序" if project_type == "plant_room" else "AHU 程序"
        result.append(f"项目类型：{label}")
    if equipment:
        result.append("设备：" + "、".join(equipment))
    if control_features:
        result.append("控制功能：" + "、".join(control_features))
    if communication:
        result.append("通讯/IO：" + "、".join(communication))
    if protection_logic:
        result.append("保护/联锁：" + "、".join(protection_logic))
    return result


def _summary(project_type: str | None, equipment: list[str], control_features: list[str], communication: list[str]) -> str:
    parts: list[str] = []
    if project_type:
        parts.append("机房群控" if project_type == "plant_room" else "AHU")
    if equipment:
        parts.append("设备 " + "、".join(equipment[:5]))
    if control_features:
        parts.append("功能 " + "、".join(control_features[:5]))
    if communication:
        parts.append("通讯/IO " + "、".join(communication[:3]))
    return "；".join(parts) if parts else "尚未形成可用于模板选择的结构化需求。"
