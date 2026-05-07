from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.services.requirement_analysis import (
    COMMUNICATION_KEYWORDS,
    CONTROL_KEYWORDS,
    EQUIPMENT_KEYWORDS,
    PROTECTION_KEYWORDS,
)
from app.services.retrieval import infer_feature_hits, normalize_project_type


SLOT_COLLECTIONS = {
    "equipment",
    "control_features",
    "communication",
    "io_points",
    "protection_logic",
    "design_constraints",
}

COUNTABLE_EQUIPMENT = {
    "风冷热泵",
    "主机",
    "水泵",
    "冷冻泵",
    "冷却泵",
    "冷却塔",
    "旁通阀",
    "AHU",
    "送风机",
    "排风机",
    "直膨机",
    "新风阀",
    "回风阀",
    "水阀",
    "电加热",
}

AHU_EXTRA_EQUIPMENT = {
    "回风阀": ["回风阀", "回风"],
    "水阀": ["水阀", "冷水阀", "热水阀"],
    "电加热": ["电加热"],
    "加湿": ["加湿"],
    "除湿": ["除湿"],
    "过滤网": ["过滤网", "滤网"],
}

PLANT_EXTRA_EQUIPMENT = {
    "冷冻泵": ["冷冻泵"],
    "冷却泵": ["冷却泵"],
    "蝶阀": ["蝶阀"],
}

TASK_KEYWORDS = {
    "new_program": ["做", "新建", "创建", "生成", "设计"],
    "modify_existing": ["改", "修改", "重命名", "替换", "新增", "接入", "断开", "复制", "调整"],
    "tune_parameter": ["参数", "设定", "阈值", "常量", "PID"],
    "map_points": ["点位", "地址", "通道", "IO", "Modbus", "BACnet", "映射"],
    "add_equipment": ["新增", "增加", "加一台", "加两台", "加3台", "扩容"],
    "add_strategy": ["策略", "控制", "联动", "轮询", "轮值", "加减载"],
}

OVERRIDE_TOKENS = ("改成", "调整为", "改为", "变成", "换成")
REJECT_TOKENS = ("不是", "不要", "取消", "不需要", "去掉", "删除", "移除")
COUNT_PATTERN = re.compile(
    r"(?P<count>\d+|一|两|二|三|四|五|六|七|八|九|十)\s*(?:台|个|路|点)?\s*(?P<name>风冷热泵|热泵|冷冻泵|冷却泵|水泵|主机|冷机|冷水机组|冷却塔|旁通阀|送风机|排风机|直膨机|新风阀|回风阀|水阀|电加热|蝶阀)"
)
EQUIPMENT_THEN_COUNT_PATTERN = re.compile(
    r"(?P<name>风冷热泵|热泵|冷冻泵|冷却泵|水泵|主机|冷机|冷水机组|冷却塔|旁通阀|送风机|排风机|直膨机|新风阀|回风阀|水阀|电加热|蝶阀)\s*(?:改成|调整为|改为|变成|换成)\s*(?P<count>\d+|一|两|二|三|四|五|六|七|八|九|十)\s*(?:台|个|路|点)"
)


def merge_requirement_slots(
    *,
    message: str,
    rule_summary: dict[str, Any],
    llm_result: dict[str, Any] | None = None,
    existing_slots: dict[str, Any] | None = None,
    current_project_path: str | None = None,
) -> dict[str, Any]:
    """合并单轮需求到稳定槽位，并保留来源和覆盖状态。"""

    slots = _initial_slots(existing_slots)
    if not existing_slots:
        _bootstrap_from_rule_history(slots, rule_summary, current_message=message)
    message_id = f"msg_{len(slots['message_history']) + 1}"
    slots["message_history"].append({"message_id": message_id, "content": message})
    slots["latest_message_id"] = message_id

    project_type = _project_type_from(rule_summary, llm_result, message) or slots.get("project_type")
    if project_type in {"plant_room", "ahu"}:
        slots["project_type"] = project_type

    slots["task_type"] = _infer_task_type(message, current_project_path=current_project_path)
    if current_project_path:
        slots["current_project_path"] = current_project_path

    extracted = _extract_from_rule_text(message)
    _merge_extracted(slots, extracted, message_id=message_id, source="rule", message=message)
    if llm_result:
        _merge_extracted(slots, _extract_from_llm(llm_result), message_id=message_id, source="llm", message=message)
        slots["llm_risk_level"] = llm_result.get("risk_level")
        for reason in _string_list(llm_result.get("risk_reasons")):
            _append_unique(slots["risk_items"], reason)
        slots["llm_status"] = "merged"
        slots["llm_meta"] = llm_result.get("llm_meta")
    else:
        slots.setdefault("llm_status", "not_used")

    _refresh_missing_and_risk(slots, message=message, current_project_path=current_project_path)
    slots["source"] = _source_label(slots)
    return slots


def requirement_summary_from_slots(slots: dict[str, Any]) -> dict[str, Any]:
    active = {field: _active_values(slots, field) for field in SLOT_COLLECTIONS}
    project_type = slots.get("project_type") if slots.get("project_type") in {"plant_room", "ahu"} else None
    raw_requirements = [str(item.get("content", "")) for item in slots.get("message_history", []) if isinstance(item, dict)]
    requested_features = sorted(infer_feature_hits("\n".join(raw_requirements + _flatten_active(active))))
    missing_fields = list(slots.get("missing_fields") or [])
    blocking_missing_fields = list(slots.get("blocking_missing_fields") or [])
    questions = list(slots.get("clarification_questions") or [])
    open_questions = [{"field": field, "question": question} for field, question in zip(missing_fields, questions)]

    confirmed_requirements = _confirmed_requirements(
        project_type=project_type,
        equipment=_format_equipment(slots),
        control_features=active["control_features"],
        communication=active["communication"],
        io_points=active["io_points"],
        protection_logic=active["protection_logic"],
    )
    return {
        "raw_requirements": raw_requirements,
        "latest_user_message": raw_requirements[-1] if raw_requirements else "",
        "project_type": project_type,
        "task_type": slots.get("task_type"),
        "equipment": _format_equipment(slots),
        "equipment_items": _active_items(slots, "equipment"),
        "control_features": active["control_features"],
        "communication": active["communication"],
        "io_points": active["io_points"],
        "protection_logic": active["protection_logic"],
        "design_constraints": active["design_constraints"],
        "requested_features": requested_features,
        "missing_fields": missing_fields,
        "blocking_missing_fields": blocking_missing_fields,
        "clarification_questions": questions,
        "open_questions": open_questions,
        "confirmed_requirements": confirmed_requirements,
        "ready_for_template_search": not blocking_missing_fields,
        "risk_level": slots.get("risk_level", "low"),
        "risk_items": list(slots.get("risk_items") or []),
        "summary": _summary(project_type, _format_equipment(slots), active["control_features"], active["communication"]),
    }


def _initial_slots(existing_slots: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(existing_slots, dict) and existing_slots:
        slots = deepcopy(existing_slots)
    else:
        slots = {
            "project_type": None,
            "task_type": "new_program",
            "equipment": [],
            "control_features": [],
            "communication": [],
            "io_points": [],
            "protection_logic": [],
            "design_constraints": [],
            "missing_fields": [],
            "blocking_missing_fields": [],
            "clarification_questions": [],
            "risk_level": "low",
            "risk_items": [],
            "source": "rule",
            "message_history": [],
            "slot_sources": {},
            "slot_status": {},
        }
    for field in SLOT_COLLECTIONS:
        slots.setdefault(field, [])
    slots.setdefault("message_history", [])
    slots.setdefault("slot_sources", {})
    slots.setdefault("slot_status", {})
    slots.setdefault("risk_items", [])
    return slots


def _bootstrap_from_rule_history(slots: dict[str, Any], rule_summary: dict[str, Any], *, current_message: str) -> None:
    raw_requirements = rule_summary.get("raw_requirements")
    if not isinstance(raw_requirements, list) or len(raw_requirements) <= 1:
        return
    history = [str(item) for item in raw_requirements]
    if history and history[-1] == current_message:
        history = history[:-1]
    for content in history:
        message_id = f"msg_{len(slots['message_history']) + 1}"
        slots["message_history"].append({"message_id": message_id, "content": content})
        _merge_extracted(slots, _extract_from_rule_text(content), message_id=message_id, source="rule", message=content)


def _project_type_from(rule_summary: dict[str, Any], llm_result: dict[str, Any] | None, message: str) -> str | None:
    llm_type = llm_result.get("project_type") if isinstance(llm_result, dict) else None
    if llm_type in {"plant_room", "ahu"} and float(llm_result.get("project_type_confidence") or 0) >= 0.5:
        return str(llm_type)
    rule_type = rule_summary.get("project_type")
    if rule_type in {"plant_room", "ahu"}:
        return str(rule_type)
    return normalize_project_type(message)


def _infer_task_type(message: str, *, current_project_path: str | None) -> str:
    if current_project_path:
        return "modify_existing"
    for task_type, keywords in TASK_KEYWORDS.items():
        if any(keyword in message for keyword in keywords):
            return task_type
    return "new_program"


def _extract_from_rule_text(message: str) -> dict[str, list[Any]]:
    equipment_map = {**EQUIPMENT_KEYWORDS, **AHU_EXTRA_EQUIPMENT, **PLANT_EXTRA_EQUIPMENT}
    equipment = [{"name": name} for name in _extract_keywords(message, equipment_map) if name != "AHU"]
    counts = _extract_counts(message)
    for item in equipment:
        if item["name"] in counts:
            item["quantity"] = counts[item["name"]]
    for name, quantity in counts.items():
        if not any(item["name"] == name for item in equipment):
            equipment.append({"name": name, "quantity": quantity})

    return {
        "equipment": equipment,
        "control_features": _extract_keywords(message, CONTROL_KEYWORDS),
        "communication": _extract_keywords(message, COMMUNICATION_KEYWORDS),
        "io_points": _extract_io_points(message),
        "protection_logic": _extract_keywords(message, PROTECTION_KEYWORDS),
        "design_constraints": _extract_constraints(message),
    }


def _extract_from_llm(llm_result: dict[str, Any]) -> dict[str, list[Any]]:
    return {
        "equipment": [_coerce_equipment_item(item) for item in llm_result.get("equipment") or []],
        "control_features": _string_list(llm_result.get("control_features")),
        "communication": _string_list(llm_result.get("communication")),
        "io_points": _string_list(llm_result.get("io_points")),
        "protection_logic": _string_list(llm_result.get("protection_logic")),
        "design_constraints": [],
    }


def _merge_extracted(slots: dict[str, Any], extracted: dict[str, list[Any]], *, message_id: str, source: str, message: str) -> None:
    for field, values in extracted.items():
        for value in values:
            if field == "equipment":
                item = _coerce_equipment_item(value)
                name = item.get("name")
                if not name:
                    continue
                _merge_slot_item(
                    slots,
                    field,
                    key=str(name),
                    display=str(name),
                    message_id=message_id,
                    source=source,
                    status=_status_for_value(message, str(name)),
                    quantity=item.get("quantity"),
                )
            else:
                text = _normalize_slot_value(field, str(value).strip())
                if not text:
                    continue
                _merge_slot_item(
                    slots,
                    field,
                    key=text,
                    display=text,
                    message_id=message_id,
                    source=source,
                    status=_status_for_value(message, text),
                )


def _merge_slot_item(
    slots: dict[str, Any],
    field: str,
    *,
    key: str,
    display: str,
    message_id: str,
    source: str,
    status: str,
    quantity: int | None = None,
) -> None:
    items = slots[field]
    existing = next((item for item in items if isinstance(item, dict) and item.get("key") == key), None)
    old_quantity = existing.get("quantity") if isinstance(existing, dict) else None

    if existing is None:
        existing = {
            "key": key,
            "name": display,
            "status": status,
            "sources": [],
            "source": source,
            "updated_at_message_id": message_id,
        }
        items.append(existing)
    else:
        existing["status"] = status
        existing["source"] = _merge_source(str(existing.get("source") or ""), source)
        existing["updated_at_message_id"] = message_id

    if quantity is not None:
        existing["quantity"] = quantity
        if old_quantity not in {None, quantity}:
            existing["previous_quantity"] = old_quantity
            existing["status"] = "confirmed"
            _append_unique(slots["risk_items"], f"设备数量变化：{display} {old_quantity} -> {quantity}")

    _append_unique(existing["sources"], message_id)
    slot_key = f"{field}:{key}"
    slots["slot_sources"][slot_key] = list(existing["sources"])
    slots["slot_status"][slot_key] = existing["status"]


def _status_for_value(message: str, value: str) -> str:
    if _token_applies_to_value(message, value, REJECT_TOKENS):
        return "rejected"
    if _token_applies_to_value(message, value, OVERRIDE_TOKENS):
        return "confirmed"
    return "confirmed"


def _token_applies_to_value(message: str, value: str, tokens: tuple[str, ...]) -> bool:
    index = message.find(value)
    if index < 0:
        return False
    prefix = message[max(0, index - 4) : index].strip(" ，,、；;。")
    suffix = message[index + len(value) : index + len(value) + 4].strip(" ，,、；;。")
    return any(prefix.endswith(token) or suffix.startswith(token) for token in tokens)


def _refresh_missing_and_risk(slots: dict[str, Any], *, message: str, current_project_path: str | None) -> None:
    active_equipment = _active_values(slots, "equipment")
    active_control = _active_values(slots, "control_features")
    active_communication = _active_values(slots, "communication")
    active_protection = _active_values(slots, "protection_logic")
    project_type = slots.get("project_type")

    missing_fields: list[str] = []
    blocking: list[str] = []
    questions: list[str] = []
    if not project_type:
        missing_fields.append("project_type")
        blocking.append("project_type")
        questions.append("请先确认项目类型：机房群控程序还是 AHU 程序？")
    if project_type and not current_project_path and not active_equipment and not active_control:
        missing_fields.append("equipment_or_control_goal")
        blocking.append("equipment_or_control_goal")
        questions.append("请补充主要设备或控制目标，例如水泵、旁通阀、直膨机、排风机或 CO2 控制。")
    if project_type and not active_communication:
        missing_fields.append("communication")
        questions.append("请确认通讯或 IO 方式，例如 Modbus、BACnet、MQTT 或硬接 IO。")
    if project_type and not active_protection:
        missing_fields.append("protection_logic")
        questions.append("请确认关键保护/联锁要求，例如防冻、故障报警、运行反馈或最小启停延时。")
    if project_type and not _has_any_quantity(slots):
        missing_fields.append("device_count")
        questions.append("请确认关键设备数量，例如主机、水泵、冷却塔、风机或直膨机数量。")

    risk_items = list(slots.get("risk_items") or [])
    if _mentions_io_or_comm_change(message):
        _append_unique(risk_items, "涉及 IO/通讯点位或地址变更，需要人工确认。")
    if _mentions_protection_delete(message):
        _append_unique(risk_items, "涉及删除、取消或旁路保护联锁，默认按高风险处理。")
    if any("设备数量变化" in item for item in risk_items):
        risk_level = "high"
    elif risk_items:
        risk_level = "high" if any("IO/通讯" in item or "保护" in item for item in risk_items) else "medium"
    else:
        risk_level = "low"
    llm_risk_level = slots.get("llm_risk_level")
    if llm_risk_level in {"low", "medium", "high"}:
        risk_level = _max_risk_level(risk_level, str(llm_risk_level))

    slots["missing_fields"] = missing_fields
    slots["blocking_missing_fields"] = blocking
    slots["clarification_questions"] = questions
    slots["risk_items"] = risk_items
    slots["risk_level"] = risk_level


def _confirmed_requirements(
    *,
    project_type: str | None,
    equipment: list[str],
    control_features: list[str],
    communication: list[str],
    io_points: list[str],
    protection_logic: list[str],
) -> list[str]:
    result: list[str] = []
    if project_type:
        result.append(f"项目类型：{'机房群控程序' if project_type == 'plant_room' else 'AHU 程序'}")
    if equipment:
        result.append("设备：" + "、".join(equipment))
    if control_features:
        result.append("控制功能：" + "、".join(control_features))
    if communication:
        result.append("通讯/IO：" + "、".join(communication))
    if io_points:
        result.append("点位：" + "、".join(io_points))
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


def _active_values(slots: dict[str, Any], field: str) -> list[str]:
    return [str(item.get("name") or item.get("key")) for item in _active_items(slots, field)]


def _active_items(slots: dict[str, Any], field: str) -> list[dict[str, Any]]:
    items = slots.get(field)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("status") == "confirmed"]


def _format_equipment(slots: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in _active_items(slots, "equipment"):
        name = str(item.get("name") or item.get("key"))
        quantity = item.get("quantity")
        if isinstance(quantity, int):
            result.append(f"{name} {quantity} 台")
        else:
            result.append(name)
    return result


def _extract_keywords(text: str, keyword_map: dict[str, list[str]]) -> list[str]:
    return [label for label, keywords in keyword_map.items() if any(_keyword_matches(text, keyword) for keyword in keywords)]


def _keyword_matches(text: str, keyword: str) -> bool:
    if keyword == "风机":
        index = text.find(keyword)
        while index >= 0:
            prefix = text[index - 1] if index > 0 else ""
            if prefix != "排":
                return True
            index = text.find(keyword, index + len(keyword))
        return False
    return keyword in text


def _normalize_slot_value(field: str, value: str) -> str:
    compact = value.replace(" ", "")
    aliases = {
        "control_features": {
            "CO2控制": "CO2 控制",
            "二氧化碳控制": "CO2 控制",
            "PID控制": "PID 控制",
        },
        "communication": {
            "Modbus通讯": "Modbus",
            "BACnet通讯": "BACnet",
            "MQTT通讯": "MQTT",
            "硬接IO": "硬接 IO",
        },
        "io_points": {
            "CO2浓度": "CO2",
            "二氧化碳": "CO2",
        },
    }
    return aliases.get(field, {}).get(compact, value)


def _extract_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in COUNT_PATTERN.finditer(text):
        name = _normalize_equipment_name(match.group("name"))
        count = _parse_count(match.group("count"))
        if name in COUNTABLE_EQUIPMENT and count is not None:
            counts[name] = count
    for match in EQUIPMENT_THEN_COUNT_PATTERN.finditer(text):
        name = _normalize_equipment_name(match.group("name"))
        count = _parse_count(match.group("count"))
        if name in COUNTABLE_EQUIPMENT and count is not None:
            counts[name] = count
    return counts


def _parse_count(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return mapping.get(value)


def _normalize_equipment_name(name: str) -> str:
    aliases = {"冷机": "主机", "冷水机组": "主机", "热泵": "风冷热泵"}
    return aliases.get(name, name)


def _extract_io_points(text: str) -> list[str]:
    points: list[str] = []
    for token in ("DI", "DO", "AI", "AO", "运行反馈", "故障反馈", "过滤网报警", "防冻开关", "压差", "CO2"):
        if token in text:
            points.append(token)
    return points


def _extract_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    if "保留" in text:
        constraints.append("保留现有相关逻辑")
    if "不能改" in text or "不要改" in text:
        constraints.append("指定逻辑不可修改")
    return constraints


def _coerce_equipment_item(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("equipment") or value.get("label") or "").strip()
        quantity = value.get("quantity")
    else:
        text = str(value).strip()
        counts = _extract_counts(text)
        name = _normalize_equipment_name(_strip_quantity(text))
        quantity = counts.get(name)
    item: dict[str, Any] = {"name": name}
    if isinstance(quantity, int):
        item["quantity"] = quantity
    return item


def _strip_quantity(text: str) -> str:
    match = COUNT_PATTERN.search(text)
    if match:
        return _normalize_equipment_name(match.group("name"))
    return text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _flatten_active(active: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    for values in active.values():
        result.extend(values)
    return result


def _has_any_quantity(slots: dict[str, Any]) -> bool:
    return any(isinstance(item.get("quantity"), int) for item in _active_items(slots, "equipment"))


def _mentions_io_or_comm_change(message: str) -> bool:
    change = any(token in message for token in ("改", "修改", "调整", "设置", "映射", "替换"))
    target = any(token in message for token in ("IO", "点位", "地址", "通道", "Modbus", "BACnet", "对象号"))
    return change and target


def _mentions_protection_delete(message: str) -> bool:
    delete = any(token in message for token in REJECT_TOKENS)
    protection = any(token in message for token in ("保护", "联锁", "连锁", "防冻", "故障", "报警", "反馈"))
    return delete and protection


def _merge_source(left: str, right: str) -> str:
    values = [item for item in left.split("+") if item]
    _append_unique(values, right)
    return "+".join(values)


def _append_unique(items: list[Any], value: Any) -> None:
    if value not in items:
        items.append(value)


def _source_label(slots: dict[str, Any]) -> str:
    sources: set[str] = set()
    for field in SLOT_COLLECTIONS:
        for item in slots.get(field) or []:
            if isinstance(item, dict) and item.get("source"):
                sources.update(str(item["source"]).split("+"))
    return "merged" if {"rule", "llm"}.issubset(sources) else ("llm" if "llm" in sources else "rule")


def _max_risk_level(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 0) >= order.get(right, 0) else right
