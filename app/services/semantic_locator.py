from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.project_semantic_index import build_project_semantic_index, summarize_affected_nodes


REFERENCE_KEYWORDS = ("刚才", "刚刚", "上次", "这个", "那个", "它", "其", "该节点", "最近")

TYPE_ALIASES = {
    "比较判断": "compare",
    "比较": "compare",
    "阈值": "compare",
    "逻辑运算": "logic",
    "逻辑模块": "logic",
    "通道选择": "switch",
    "开关模块": "switch",
    "加法运算": "add",
    "加法模块": "add",
    "加法": "add",
    "求和": "add",
    "减法运算": "subtract",
    "减法模块": "subtract",
    "减法": "subtract",
    "乘法运算": "multiply",
    "乘法模块": "multiply",
    "乘法": "multiply",
    "除法运算": "divide",
    "除法模块": "divide",
    "除法": "divide",
    "取位运算": "bitFetch",
    "取位模块": "bitFetch",
    "取位": "bitFetch",
    "延时开": "delayOn",
    "延时启动": "delayOn",
    "延时关": "delayOff",
    "延时关闭": "delayOff",
    "常量": "constInput",
    "常数": "constInput",
    "设定": "constInput",
    "软件输入": "swInput",
    "物理输入": "hwInput",
    "硬接输入": "hwInput",
    "物理输出": "hwOutput",
    "硬接输出": "hwOutput",
    "PID控制器": "pid",
    "pid控制器": "pid",
    "PID": "pid",
    "pid": "pid",
    "Modbus": "modbusOutput",
    "modbus": "modbusOutput",
    "BACnet": "bacipOutput",
    "BACIP": "bacipOutput",
}


def locate_semantic_targets(
    message: str,
    *,
    project_path: str | Path,
    project_type: str | None = None,
    template_id: str | None = None,
    conversation_context: dict[str, Any] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """把用户自然语言定位为当前工程里的候选目标。"""

    del project_type, template_id
    context = conversation_context or {}
    intent = _infer_intent(message)
    target_queries = _target_queries(message)
    knowledge_queries = _knowledge_queries(message, intent)

    if _has_recent_reference(message):
        recent = _locate_recent_reference(message, project_path=project_path, conversation_context=context, intent=intent, limit=limit)
        if recent is not None:
            return {**recent, "target_queries": target_queries, "knowledge_queries": knowledge_queries}

    index = build_project_semantic_index(project_path)
    explicit_node = _node_from_explicit_id(index, message)
    if explicit_node:
        selected = _candidate_from_index_node(explicit_node, index=1, confidence=0.99, reason="用户输入中包含明确节点 ID。")
        return {
            "status": "resolved",
            "intent": intent,
            "selected": selected,
            "candidates": [selected],
            "questions": [],
            "target_queries": target_queries,
            "knowledge_queries": knowledge_queries,
        }
    tab_label_target = _tab_label_update_target(index, message)
    if tab_label_target:
        selected = _candidate_from_tab(tab_label_target, index=1, confidence=0.95, reason="用户明确要求修改页面标签。")
        return {
            "status": "resolved",
            "intent": "update_param",
            "selected": selected,
            "candidates": [selected],
            "questions": [],
            "target_queries": target_queries,
            "knowledge_queries": knowledge_queries,
        }
    tab = _extract_tab(index, message)
    node_type = _infer_node_type(message)
    name_hint = _extract_name_hint(message, tab_label=tab["label"] if tab else None)
    candidates = _filter_candidates(index, message, tab=tab, node_type=node_type, name_hint=name_hint, limit=limit)
    if not candidates and name_hint and (tab or node_type):
        candidates = _filter_candidates(index, message, tab=tab, node_type=node_type, name_hint=None, limit=limit)

    if len(candidates) == 1:
        selected = {**candidates[0], "confidence": max(float(candidates[0]["confidence"]), 0.88), "reason": "页面、类型或名称约束后唯一匹配。"}
        return {
            "status": "resolved",
            "intent": intent,
            "selected": selected,
            "candidates": [selected],
            "questions": [],
            "target_queries": target_queries,
            "knowledge_queries": knowledge_queries,
        }

    if candidates:
        question = _candidate_question(candidates, message=message)
        return {
            "status": "candidates",
            "intent": intent,
            "selected": None,
            "candidates": candidates,
            "questions": [question],
            "target_queries": target_queries,
            "knowledge_queries": knowledge_queries,
        }

    return {
        "status": "needs_clarification",
        "intent": intent,
        "selected": None,
        "candidates": [],
        "questions": [_missing_target_question(message)],
        "target_queries": target_queries,
        "knowledge_queries": knowledge_queries,
    }


def public_semantic_candidates(location: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(location, dict):
        return []
    candidates = location.get("candidates")
    if not isinstance(candidates, list):
        return []
    result: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        result.append(
            {
                "candidate_id": candidate.get("candidate_id") or f"candidate_{index}",
                "kind": candidate.get("kind", "node"),
                "display_name": candidate.get("display_name", ""),
                "description": candidate.get("description", ""),
                "confidence": candidate.get("confidence", 0),
                "selector": candidate.get("selector"),
                "tab_label": candidate.get("tab_label"),
                "type": candidate.get("type"),
                "key_params": candidate.get("key_params", {}),
            }
        )
    return result


def _locate_recent_reference(
    message: str,
    *,
    project_path: str | Path,
    conversation_context: dict[str, Any],
    intent: str,
    limit: int,
) -> dict[str, Any] | None:
    affected = conversation_context.get("last_affected_node_ids")
    if not isinstance(affected, list):
        affected = []
    affected_ids = [str(item) for item in affected if isinstance(item, str) and item]
    if not affected_ids:
        return {
            "status": "needs_clarification",
            "intent": intent,
            "selected": None,
            "candidates": [],
            "questions": ["我还没有可引用的上一次成功修改目标。请说明目标所在页面、节点名称或业务对象。"],
        }
    candidates = [
        _candidate_from_touched_entity(entity, index=index, confidence=0.96, reason="用户使用了最近目标引用。")
        for index, entity in enumerate(summarize_affected_nodes(project_path, affected_ids, limit=limit), start=1)
    ]
    candidates = [candidate for candidate in candidates if candidate]
    if len(candidates) == 1:
        return {
            "status": "resolved",
            "intent": intent,
            "selected": candidates[0],
            "candidates": candidates,
            "questions": [],
        }
    return {
        "status": "candidates",
        "intent": intent,
        "selected": None,
        "candidates": candidates,
        "questions": [_candidate_question(candidates, message=message)],
    }


def _filter_candidates(
    index: dict[str, Any],
    message: str,
    *,
    tab: dict[str, Any] | None,
    node_type: str | None,
    name_hint: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    nodes = index.get("nodes") if isinstance(index.get("nodes"), list) else []
    filtered: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if tab and node.get("tab_id") != tab.get("id"):
            continue
        if node_type and node.get("type") != node_type:
            continue
        if name_hint and name_hint not in str(node.get("name", "")) and name_hint not in str(node.get("label", "")):
            continue
        if not tab and not node_type and not name_hint:
            continue
        filtered.append(node)

    if not filtered:
        return []
    scored = [(_score_node(node, message, tab=tab, node_type=node_type, name_hint=name_hint), node) for node in filtered]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_candidate_from_index_node(node, index=idx, confidence=score, reason=_candidate_reason(tab=tab, node_type=node_type, name_hint=name_hint)) for idx, (score, node) in enumerate(scored[:limit], start=1)]


def _node_from_explicit_id(index: dict[str, Any], message: str) -> dict[str, Any] | None:
    match = re.search(r"\b[a-fA-F0-9]{5,12}\b", message)
    if not match:
        return None
    node_id = match.group(0)
    nodes = index.get("nodes") if isinstance(index.get("nodes"), list) else []
    for node in nodes:
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return None


def _score_node(
    node: dict[str, Any],
    message: str,
    *,
    tab: dict[str, Any] | None,
    node_type: str | None,
    name_hint: str | None,
) -> float:
    score = 0.45
    if tab:
        score += 0.2
    if node_type and node.get("type") == node_type:
        score += 0.18
    if name_hint and (name_hint in str(node.get("name", "")) or name_hint in str(node.get("label", ""))):
        score += 0.18
    if str(node.get("name", "")) and str(node.get("name")) in message:
        score += 0.08
    if node.get("tab_label"):
        score += 0.05
    if node.get("input_sources"):
        score += 0.02
    return round(min(score, 0.98), 4)


def _candidate_from_index_node(node: dict[str, Any], *, index: int, confidence: float, reason: str) -> dict[str, Any]:
    return {
        "candidate_id": f"candidate_{index}",
        "kind": "node",
        "confidence": confidence,
        "selector": {"id": node.get("id")},
        "display_name": node.get("display_name") or _display_name(node),
        "description": node.get("description") or _description(node),
        "reason": reason,
        "tab_id": node.get("tab_id"),
        "tab_label": node.get("tab_label"),
        "type": node.get("type"),
        "name": node.get("name"),
        "label": node.get("label"),
        "key_params": node.get("key_params", {}),
        "node": {
            "id": node.get("id"),
            "type": node.get("type"),
            "tab_id": node.get("tab_id"),
            "tab_label": node.get("tab_label"),
            "name": node.get("name"),
            "label": node.get("label"),
            "key_params": node.get("key_params", {}),
        },
    }


def _candidate_from_touched_entity(entity: dict[str, Any], *, index: int, confidence: float, reason: str) -> dict[str, Any]:
    return {
        "candidate_id": f"candidate_{index}",
        "kind": entity.get("kind", "node"),
        "confidence": confidence,
        "selector": entity.get("selector"),
        "display_name": entity.get("display_name", ""),
        "description": entity.get("description", ""),
        "reason": reason,
        "tab_id": entity.get("tab_id"),
        "tab_label": entity.get("tab_label"),
        "type": entity.get("type"),
        "name": entity.get("name"),
        "label": entity.get("label"),
        "key_params": entity.get("key_params", {}),
        "node": {
            "id": entity.get("id"),
            "type": entity.get("type"),
            "tab_id": entity.get("tab_id"),
            "tab_label": entity.get("tab_label"),
            "name": entity.get("name"),
            "label": entity.get("label"),
            "key_params": entity.get("key_params", {}),
        },
    }


def _candidate_from_tab(tab: dict[str, Any], *, index: int, confidence: float, reason: str) -> dict[str, Any]:
    return {
        "candidate_id": f"candidate_{index}",
        "kind": "tab",
        "confidence": confidence,
        "selector": {"id": tab.get("id")},
        "display_name": f"页面 / {tab.get('label', '')}",
        "description": "页面标签",
        "reason": reason,
        "tab_id": tab.get("id"),
        "tab_label": tab.get("label"),
        "type": "tab",
        "name": None,
        "label": tab.get("label"),
        "key_params": {"label": tab.get("label")},
        "node": {
            "id": tab.get("id"),
            "type": "tab",
            "tab_id": tab.get("id"),
            "tab_label": tab.get("label"),
            "name": None,
            "label": tab.get("label"),
            "key_params": {"label": tab.get("label")},
        },
    }


def _candidate_question(candidates: list[dict[str, Any]], *, message: str) -> str:
    del message
    lines = ["我找到了多个可能的目标，请确认要修改哪一个："]
    for index, candidate in enumerate(candidates, start=1):
        description = str(candidate.get("description") or "").strip()
        suffix = f" / {description}" if description else ""
        lines.append(f"{index}. {candidate.get('display_name', '')}{suffix}")
    return "\n".join(lines)


def _missing_target_question(message: str) -> str:
    if any(keyword in message for keyword in ("断开", "连接", "接入", "接到")):
        return "请说明目标所在页面、业务对象和接线位置，例如接到比较判断的动态阈值输入或 PID 的设定输入。"
    return "请说明目标所在页面、节点名称或业务对象。例如：把水泵控制里的比较判断改名为水泵比较判断。"


def _candidate_reason(*, tab: dict[str, Any] | None, node_type: str | None, name_hint: str | None) -> str:
    parts: list[str] = []
    if tab:
        parts.append(f"页面匹配：{tab.get('label')}")
    if node_type:
        parts.append(f"节点类型匹配：{node_type}")
    if name_hint:
        parts.append(f"名称匹配：{name_hint}")
    return "；".join(parts) if parts else "按当前工程语义索引匹配。"


def _extract_tab(index: dict[str, Any], message: str) -> dict[str, Any] | None:
    tabs = [tab for tab in index.get("tabs", []) if isinstance(tab, dict)]
    matches = [
        tab
        for tab in sorted(tabs, key=lambda item: len(str(item.get("label", ""))), reverse=True)
        if _tab_label_matches(str(tab.get("label", "")), message)
    ]
    return matches[0] if matches else None


def _tab_label_update_target(index: dict[str, Any], message: str) -> dict[str, Any] | None:
    if not any(keyword in message for keyword in ("页面标签", "页面名称", "页面名", "页签标签", "tab标签", "tab label")):
        return None
    if not any(keyword in message for keyword in ("改成", "改为", "替换为", "改名为")):
        return None
    return _extract_tab(index, message)


def _tab_label_matches(label: str, message: str) -> bool:
    if not label:
        return False
    return label in message or f"{label}页面" in message or f"{label}页" in message or f"{label}页签" in message


def _infer_node_type(message: str) -> str | None:
    for keyword, node_type in TYPE_ALIASES.items():
        if keyword in message:
            return node_type
    return None


def _infer_intent(message: str) -> str:
    if "改名" in message:
        return "rename_node"
    if any(keyword in message for keyword in ("断开", "断掉", "取消连接", "移除连接")):
        return "disconnect"
    if any(keyword in message for keyword in ("接入", "接到", "连接", "连线")):
        return "connect"
    if any(keyword in message for keyword in ("新增", "添加", "创建", "新建")):
        return "add_logic"
    if any(keyword in message for keyword in ("点位", "通道", "地址", "IO", "io", "Modbus", "BACnet")):
        return "set_io_point"
    if any(keyword in message for keyword in ("阈值", "常量值", "设定值", "改为", "改成", "调整为", "设为", "设置为")):
        return "update_param"
    return "unknown"


def _extract_name_hint(message: str, *, tab_label: str | None) -> str | None:
    text = re.split(r"改名为|改为|设为|设置为|调整为|接入|接到|连接|断开", message, maxsplit=1)[0]
    text = re.sub(r"^(把|将|请|帮我)", "", text).strip()
    if tab_label:
        text = text.replace(tab_label, "")
    for keyword in list(TYPE_ALIASES):
        text = text.replace(keyword, "")
    text = re.sub(r"(里面|里的|中|里|内|下|那个|这个|刚才|刚刚|上次|节点|的)", " ", text)
    text = re.sub(r"\s+", " ", text).strip().strip("\"'“”‘’`")
    return text or None


def _has_recent_reference(message: str) -> bool:
    return any(keyword in message for keyword in REFERENCE_KEYWORDS)


def _target_queries(message: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", message.strip())
    return [cleaned] if cleaned else []


def _knowledge_queries(message: str, intent: str) -> list[str]:
    cleaned = message.strip()
    queries = [cleaned] if cleaned else []
    if intent in {"connect", "add_logic", "update_param"}:
        _append_unique(queries, f"{cleaned} 补丁 配方")
    for recipe_query in _recipe_queries(cleaned):
        _append_unique(queries, recipe_query)
    return queries


def _recipe_queries(message: str) -> list[str]:
    queries: list[str] = []
    if not message:
        return queries
    if "CO2" in message or "二氧化碳" in message:
        _append_unique(queries, "AHU CO2 新风阀 比较判断 动态阈值 补丁配方")
        _append_unique(queries, "CO2 浓度设定 接入 新风阀 控制 补丁 配方")
    if "送风" in message and ("PID" in message or "pid" in message):
        _append_unique(queries, "AHU 送风温度 PID 动态设定 补丁配方")
    if ("PID" in message or "pid" in message) and any(token in message for token in ("输出上限", "输出下限", "highPidOutLimit", "lowPidOutLimit", "动态")):
        _append_unique(queries, "PID 输出上限 下限 动态输入 补丁配方")
    if "过滤网" in message:
        _append_unique(queries, "AHU 过滤网 报警 点位 补丁配方")
    if "防冻" in message:
        _append_unique(queries, "AHU 防冻保护 禁止旁路 补丁配方")
    if "水泵" in message and any(token in message for token in ("阈值", "台数", "比较判断", "运行台数")):
        _append_unique(queries, "机房 水泵 运行台数 阈值 比较判断 补丁配方")
    if "旁通" in message and "压差" in message:
        if any(token in message for token in ("新增", "接入", "接到", "动态")):
            _append_unique(queries, "机房 旁通阀 压差设定 新增 接入 比较判断 补丁配方")
        _append_unique(queries, "机房 旁通阀 压差设定 修改 补丁配方")
    if "比较判断" in message and any(token in message for token in ("动态", "接入", "接到", "阈值")):
        _append_unique(queries, "比较判断 动态阈值 输入 target_input=1 补丁配方")
    if any(token in message for token in ("IO", "io", "I/O", "点位", "通道", "地址", "Modbus", "BACnet", "对象号")):
        _append_unique(queries, "IO 通讯 点位 修改 风险 set_io_point 补丁配方")
    if any(token in message for token in ("copy_block", "复制", "功能块", "克隆")):
        _append_unique(queries, "copy_block 功能块 边界接线 确认 补丁配方")
    if any(token in message for token in ("断开保护", "删除保护", "保护链路", "联锁")):
        _append_unique(queries, "删除 断开 保护链路 禁止 确认 补丁配方")
    return queries


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _display_name(node: dict[str, Any]) -> str:
    parts = [str(node.get("tab_label") or ""), str(node.get("name") or node.get("label") or ""), str(node.get("schema_name") or node.get("type") or "")]
    return " / ".join(part for part in parts if part)


def _description(node: dict[str, Any]) -> str:
    parts: list[str] = []
    key_params = node.get("key_params") if isinstance(node.get("key_params"), dict) else {}
    for field in ("tripPoint", "fixedValue", "outOfServiceValue", "as", "inputAuxEnable"):
        if field in key_params:
            parts.append(f"{field}={key_params[field]}")
    upstream = node.get("upstream_labels") or []
    if upstream:
        parts.append("上游：" + "、".join(str(item) for item in upstream[:3]))
    return "；".join(parts)
