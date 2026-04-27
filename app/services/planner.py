from __future__ import annotations

import re
from typing import Any

from app.services.json_project import find_nodes, get_tabs, load_project
from app.services.knowledge import search_knowledge
from app.services.retrieval import search_nodes, search_tabs


class PlannerError(ValueError):
    """补丁规划失败。"""


TYPE_KEYWORDS = {
    "比较判断": "compare",
    "比较": "compare",
    "常量": "constInput",
    "PID": "pid",
    "pid": "pid",
    "延时开": "delayOn",
    "延时关": "delayOff",
}

FIELD_ALIASES = {
    "fixedValue": "fixedValue",
    "固定值": "fixedValue",
    "常量值": "fixedValue",
    "设定值": "fixedValue",
    "tripPoint": "tripPoint",
    "阈值": "tripPoint",
    "门限": "tripPoint",
    "outOfServiceValue": "outOfServiceValue",
    "离线值": "outOfServiceValue",
    "name": "name",
    "名称": "name",
}


def plan_patch_request(
    message: str,
    *,
    project_path: str,
    template_id: str | None = None,
    project_type: str | None = None,
) -> dict[str, Any]:
    """将简单自然语言修改请求规划为结构化补丁。

    第一版只支持低风险操作。无法唯一定位时返回 needs_clarification。
    """

    nodes = load_project(project_path)
    knowledge = search_knowledge(message, limit=3)
    related_tabs = search_tabs(message, template_id=template_id, limit=3) if template_id else []
    related_nodes = search_nodes(message, template_id=template_id, limit=5) if template_id else []

    context = {
        "project_type": project_type,
        "knowledge": knowledge,
        "related_tabs": related_tabs,
        "related_nodes": related_nodes,
    }

    comment_plan = _plan_add_comment(message, nodes)
    if comment_plan:
        return {**comment_plan, **context}

    rename_plan = _plan_rename(message, nodes)
    if rename_plan:
        return {**rename_plan, **context}

    update_plan = _plan_update_param(message, nodes)
    if update_plan:
        return {**update_plan, **context}

    return {
        "status": "needs_clarification",
        "pending_patch": None,
        "questions": ["请说明要修改的节点、页面、参数和值。例如：把节点 3a4c97e 改名为 水泵比较判断。"],
        "reason": "当前请求无法被确定性规划器识别为低风险补丁。",
        **context,
    }


def _plan_add_comment(message: str, nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not any(keyword in message for keyword in ["添加备注", "新增备注", "加备注"]):
        return None

    text_match = re.search(r"(?:添加备注|新增备注|加备注)\s*[:：]?\s*(?P<text>.+)$", message)
    text = text_match.group("text").strip() if text_match else ""
    if not text:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": ["请说明备注内容。"],
            "reason": "备注内容为空。",
        }

    tab_id, question = _resolve_tab_id_from_message(message, nodes)
    if not tab_id:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": [question or "请说明备注要添加到哪个页面。"],
            "reason": "无法唯一确定备注所在页面。",
        }

    return {
        "status": "planned",
        "pending_patch": {"op": "add_comment", "tab_selector": {"id": tab_id}, "text": text},
        "questions": [],
        "reason": "识别为添加备注请求，且已唯一确定目标页面。",
    }


def _plan_rename(message: str, nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    match = re.search(r"(?:把|将)?(?P<target>.+?)(?:节点)?改名为(?P<new_name>[^，。]+)", message)
    if not match:
        return None

    target_text = _clean_target_text(match.group("target"))
    new_name = match.group("new_name").strip()
    if not new_name:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": ["请说明新的节点名称。"],
            "reason": "改名请求缺少新名称。",
        }

    selector_result = _resolve_unique_node_selector(message, target_text, nodes)
    if selector_result["status"] != "resolved":
        return selector_result

    return {
        "status": "planned",
        "pending_patch": {"op": "rename_node", "node_selector": selector_result["selector"], "new_name": new_name},
        "questions": [],
        "reason": "识别为节点改名请求，且已唯一确定目标节点。",
        "target_node": selector_result["node"],
    }


def _plan_update_param(message: str, nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    field_name = _infer_field_name(message)
    if not field_name:
        return None
    value_match = re.search(r"(?:改为|设为|设置为|调整为)\s*(?P<value>[-+]?\d+(?:\.\d+)?|[^，。\s]+)", message)
    if not value_match:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": ["请说明要设置的新值。"],
            "reason": "参数修改请求缺少新值。",
        }

    target_text = _extract_update_target_text(message, field_name)
    selector_result = _resolve_unique_node_selector(message, target_text, nodes)
    if selector_result["status"] != "resolved":
        return selector_result

    node = selector_result["node"]
    raw_value = value_match.group("value").strip()
    new_value = _coerce_value(raw_value, node.get(field_name), field_name)

    return {
        "status": "planned",
        "pending_patch": {"op": "update_param", "node_selector": selector_result["selector"], "params": {field_name: new_value}},
        "questions": [],
        "reason": f"识别为参数修改请求，目标字段为 {field_name}，且已唯一确定目标节点。",
        "target_node": selector_result["node"],
    }


def _resolve_unique_node_selector(message: str, target_text: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    node_id = _extract_node_id(message)
    if node_id:
        matches = find_nodes(nodes, {"id": node_id})
        if len(matches) == 1:
            return {"status": "resolved", "selector": {"id": node_id}, "node": _summarize_node(matches[0])}
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": [f"未找到节点 id：{node_id}，请确认节点 id 是否正确。"],
            "reason": "指定节点 id 不存在。",
        }

    selector: dict[str, Any] = {}
    tab_label = _extract_tab_label(message, nodes)
    if tab_label:
        selector["tab_label_contains"] = tab_label

    node_type = _infer_node_type(message)
    if node_type:
        selector["type"] = node_type

    name_text = _clean_name_text(target_text, tab_label)
    if name_text and not node_type:
        selector["name_contains"] = name_text

    if not selector:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": ["请提供节点 id，或同时说明页面、节点类型、节点名称。"],
            "reason": "节点定位信息不足。",
        }

    matches = find_nodes(nodes, selector)
    if len(matches) == 1:
        node = matches[0]
        return {"status": "resolved", "selector": {"id": node["id"]}, "node": _summarize_node(node)}
    if not matches:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": [f"没有找到匹配节点，请补充节点 id 或更准确的节点名称。当前选择器：{selector}"],
            "reason": "节点选择器未匹配到节点。",
        }
    return {
        "status": "needs_clarification",
        "pending_patch": None,
        "questions": [f"匹配到 {len(matches)} 个节点，请补充节点 id 或更具体的页面/名称。"],
        "reason": "节点选择器不唯一。",
        "matched_nodes": [_summarize_node(node) for node in matches[:10]],
    }


def _resolve_tab_id_from_message(message: str, nodes: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    tabs = get_tabs(nodes)
    matched = [(tab_id, label) for tab_id, label in tabs.items() if label and label in message]
    if len(matched) == 1:
        return matched[0][0], None
    if len(matched) > 1:
        labels = [label for _, label in matched]
        return None, f"匹配到多个页面：{labels}，请指定一个页面。"
    if len(tabs) == 1:
        return next(iter(tabs)), None
    return None, "请说明备注要添加到哪个页面。"


def _extract_node_id(text: str) -> str | None:
    match = re.search(r"\b[a-fA-F0-9]{5,12}\b", text)
    return match.group(0) if match else None


def _extract_tab_label(message: str, nodes: list[dict[str, Any]]) -> str | None:
    labels = sorted(get_tabs(nodes).values(), key=len, reverse=True)
    for label in labels:
        if label and label in message:
            return label
    return None


def _infer_node_type(message: str) -> str | None:
    for keyword, node_type in TYPE_KEYWORDS.items():
        if keyword in message:
            return node_type
    return None


def _infer_field_name(message: str) -> str | None:
    for keyword, field in FIELD_ALIASES.items():
        if keyword in message:
            return field
    return None


def _extract_update_target_text(message: str, field_name: str) -> str:
    del field_name
    text = re.split(r"改为|设为|设置为|调整为", message, maxsplit=1)[0]
    text = re.sub(r"^(把|将)", "", text).strip()
    for keyword in sorted(FIELD_ALIASES, key=len, reverse=True):
        text = text.replace(keyword, "")
    return _clean_target_text(text)


def _clean_target_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^(把|将|请|帮我)", "", text).strip()
    text = text.replace("节点", "").replace("的", " ").strip()
    return text


def _clean_name_text(text: str, tab_label: str | None) -> str:
    cleaned = text
    if tab_label:
        cleaned = cleaned.replace(tab_label, "")
    for keyword in TYPE_KEYWORDS:
        cleaned = cleaned.replace(keyword, "")
    return cleaned.strip()


def _coerce_value(raw_value: str, old_value: Any, field_name: str) -> Any:
    if isinstance(old_value, bool):
        return raw_value.lower() in {"true", "1", "yes", "on", "是", "真"}
    if isinstance(old_value, int) and not isinstance(old_value, bool):
        try:
            return int(float(raw_value))
        except ValueError:
            return raw_value
    if isinstance(old_value, float):
        try:
            return float(raw_value)
        except ValueError:
            return raw_value
    if field_name in {"tripPoint", "outOfServiceValue"}:
        try:
            value = float(raw_value)
            return int(value) if value.is_integer() else value
        except ValueError:
            return raw_value
    return raw_value


def _summarize_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "z": node.get("z"),
        "name": node.get("name", ""),
        "inputs": node.get("inputs"),
        "outputs": node.get("outputs"),
    }
