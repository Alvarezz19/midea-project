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
    "常数": "constInput",
    "软件输入": "swInput",
    "物理输入": "hwInput",
    "物理输出": "hwOutput",
    "引用": "quote",
    "PID控制器": "pid",
    "pid控制器": "pid",
    "PID模块": "pid",
    "pid模块": "pid",
    "延时开": "delayOn",
    "延时关": "delayOff",
}

FIELD_ALIASES = {
    "fixedValue": "fixedValue",
    "固定值": "fixedValue",
    "常量值": "fixedValue",
    "常数值": "fixedValue",
    "输出参数": "fixedValue",
    "设定值": "fixedValue",
    "tripPoint": "tripPoint",
    "阈值": "tripPoint",
    "门限": "tripPoint",
    "比较数": "tripPoint",
    "比较值": "tripPoint",
    "静态阈值": "tripPoint",
    "outOfServiceValue": "outOfServiceValue",
    "离线值": "outOfServiceValue",
    "掉线值": "outOfServiceValue",
    "默认输出值": "outOfServiceValue",
    "无效输出值": "outOfServiceValue",
    "无效时输出": "outOfServiceValue",
    "禁止时输出": "outOfServiceValue",
    "停机输出": "outOfServiceValue",
    "disabledOutValue": "disabledOutValue",
    "禁止输出模式": "disabledOutValue",
    "不使能输出模式": "disabledOutValue",
    "pidMode": "pidMode",
    "运算方向": "pidMode",
    "proportional": "proportional",
    "比例增益": "proportional",
    "P值": "proportional",
    "integral": "integral",
    "积分增益": "integral",
    "I值": "integral",
    "derivative": "derivative",
    "微分增益": "derivative",
    "D值": "derivative",
    "highPidOutLimit": "highPidOutLimit",
    "输出上限值": "highPidOutLimit",
    "输出上限": "highPidOutLimit",
    "lowPidOutLimit": "lowPidOutLimit",
    "输出下限值": "lowPidOutLimit",
    "输出下限": "lowPidOutLimit",
    "interval": "interval",
    "计算间隔": "interval",
    "运算间隔": "interval",
    "deadBand": "deadBand",
    "死区": "deadBand",
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

    dynamic_input_plan = _plan_enable_dynamic_input(message, nodes)
    if dynamic_input_plan:
        return {**dynamic_input_plan, **context}

    replace_constant_plan = _plan_replace_constant(message, nodes)
    if replace_constant_plan:
        return {**replace_constant_plan, **context}

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

    matched_nodes = find_nodes(nodes, selector_result["selector"])
    node = matched_nodes[0]
    if field_name not in node:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": [f"目标节点不包含参数 {field_name}，请确认要修改的字段或节点。"],
            "reason": "目标节点不存在该参数，拒绝新增未知字段。",
            "target_node": _summarize_node(node),
        }

    raw_value = value_match.group("value").strip()
    new_value = _coerce_value(raw_value, node.get(field_name), field_name)

    return {
        "status": "planned",
        "pending_patch": {"op": "update_param", "node_selector": selector_result["selector"], "params": {field_name: new_value}},
        "questions": [],
        "reason": f"识别为参数修改请求，目标字段为 {field_name}，且已唯一确定目标节点。",
        "target_node": selector_result["node"],
    }


def _plan_replace_constant(message: str, nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not any(keyword in message for keyword in ["替换常量", "修改常量", "常量值", "固定值", "设定值"]):
        return None

    field_name = _infer_replace_constant_field(message)
    value_match = re.search(r"(?:改为|设为|设置为|调整为|替换为)\s*(?P<value>[-+]?\d+(?:\.\d+)?|[^，。\s]+)", message)
    if not value_match:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": ["请说明常量或设定值要替换成什么值。"],
            "reason": "replace_constant 请求缺少新值。",
        }

    target_text = _extract_update_target_text(message, field_name) if field_name else _clean_target_text(re.split(r"改为|设为|设置为|调整为|替换为", message, maxsplit=1)[0])
    selector_result = _resolve_unique_node_selector(message, target_text, nodes)
    if selector_result["status"] != "resolved":
        return selector_result

    node = find_nodes(nodes, selector_result["selector"])[0]
    field_name = field_name or _default_replace_constant_field(node)
    if not field_name or field_name not in node:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": ["目标节点不是可直接替换的常量、软件输入设定值或静态阈值，请确认目标节点。"],
            "reason": "目标节点缺少可替换值字段。",
            "target_node": _summarize_node(node),
        }

    raw_value = value_match.group("value").strip()
    new_value = _coerce_value(raw_value, node.get(field_name), field_name)

    return {
        "status": "planned",
        "pending_patch": {
            "op": "replace_constant",
            "node_selector": selector_result["selector"],
            "field": field_name,
            "value": new_value,
        },
        "questions": [],
        "reason": f"识别为替换常量/设定值请求，目标字段为 {field_name}，且已唯一确定目标节点。",
        "target_node": selector_result["node"],
    }


def _plan_enable_dynamic_input(message: str, nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not ("动态" in message and "输入" in message and any(keyword in message for keyword in ["启用", "开启", "打开"])):
        return None

    target_text = _clean_target_text(re.split(r"启用|开启|打开", message, maxsplit=1)[-1])
    selector_result = _resolve_unique_node_selector(message, target_text, nodes)
    if selector_result["status"] != "resolved":
        return selector_result

    node = find_nodes(nodes, selector_result["selector"])[0]
    node_type = node.get("type")
    input_option = _infer_dynamic_input_option(message, node_type=str(node_type))
    if node_type in {"pid", "fuzzypid", "linear"} and not input_option:
        return {
            "status": "needs_clarification",
            "pending_patch": None,
            "questions": ["请说明要启用哪个动态输入选项，例如 proportional、integral、deadBand、inputD 或 outputD。"],
            "reason": "动态输入请求缺少具体选项。",
            "target_node": _summarize_node(node),
        }

    pending_patch: dict[str, Any] = {"op": "enable_dynamic_input", "node_selector": selector_result["selector"]}
    if input_option:
        pending_patch["input_option"] = input_option

    return {
        "status": "planned",
        "pending_patch": pending_patch,
        "questions": [],
        "reason": "识别为启用动态输入端口请求，已唯一确定目标节点。",
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
    if name_text:
        exact_selector = {**selector, "name": name_text}
        exact_matches = find_nodes(nodes, exact_selector)
        if len(exact_matches) == 1:
            node = exact_matches[0]
            return {"status": "resolved", "selector": {"id": node["id"]}, "node": _summarize_node(node)}
        if len(exact_matches) > 1:
            return {
                "status": "needs_clarification",
                "pending_patch": None,
                "questions": [f"名称为 {name_text} 的节点有 {len(exact_matches)} 个，请补充节点 id 或页面。"],
                "reason": "节点名称不唯一。",
                "matched_nodes": [_summarize_node(node) for node in exact_matches[:10]],
            }
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


def _infer_replace_constant_field(message: str) -> str | None:
    if any(keyword in message for keyword in ["常量值", "固定值", "fixedValue"]):
        return "fixedValue"
    if any(keyword in message for keyword in ["默认输出值", "离线值", "掉线值", "outOfServiceValue"]):
        return "outOfServiceValue"
    if any(keyword in message for keyword in ["阈值", "门限", "tripPoint"]):
        return "tripPoint"
    return None


def _infer_dynamic_input_option(message: str, *, node_type: str) -> str | None:
    aliases = {
        "proportional": ["proportional", "比例增益", "P值"],
        "integral": ["integral", "积分增益", "I值"],
        "derivative": ["derivative", "微分增益", "D值"],
        "highPidOutLimit": ["highPidOutLimit", "输出上限值", "输出上限"],
        "lowPidOutLimit": ["lowPidOutLimit", "输出下限值", "输出下限"],
        "interval": ["interval", "运算间隔", "计算间隔"],
        "deadBand": ["deadBand", "死区"],
        "pidMode": ["pidMode", "运算方向", "模式"],
        "disabledOutValue": ["disabledOutValue", "禁止输出模式"],
        "inputD": ["inputD", "输入范围", "输入量程"],
        "outputD": ["outputD", "输出范围", "输出量程"],
    }
    if node_type in {"compare", "limit"}:
        return None
    for option, keywords in aliases.items():
        if any(keyword in message for keyword in keywords):
            return option
    return None


def _default_replace_constant_field(node: dict[str, Any]) -> str | None:
    node_type = node.get("type")
    if node_type == "constInput":
        return "fixedValue"
    if node_type == "swInput":
        return "outOfServiceValue"
    if node_type == "compare":
        return "tripPoint"
    if node_type in {"add", "subtract", "multiply", "divide"}:
        return "fixedValue"
    return None


def _extract_update_target_text(message: str, field_name: str) -> str:
    text = re.split(r"改为|设为|设置为|调整为", message, maxsplit=1)[0]
    text = re.sub(r"^(把|将)", "", text).strip()
    field_keywords = [keyword for keyword, field in FIELD_ALIASES.items() if field == field_name]
    for keyword in sorted(field_keywords, key=len, reverse=True):
        text = re.sub(rf"\s*的\s*{re.escape(keyword)}\s*$", "", text)
    return _clean_target_text(text)


def _clean_target_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^(把|将|请|帮我)", "", text).strip()
    text = _extract_named_target(text) or text
    text = text.replace("节点", "").replace("的", " ").strip()
    text = _strip_wrapping_quotes(text)
    return text


def _clean_name_text(text: str, tab_label: str | None) -> str:
    cleaned = _extract_named_target(text) or text
    if tab_label:
        cleaned = cleaned.replace(tab_label, "")
    for keyword in TYPE_KEYWORDS:
        cleaned = cleaned.replace(keyword, "")
    cleaned = re.sub(r"^(页面|页|中|里|内|下|名称|名字|为|叫做|名为)+", "", cleaned.strip())
    cleaned = re.sub(r"(页面|页|中|里|内|下)$", "", cleaned.strip())
    cleaned = _strip_wrapping_quotes(cleaned)
    return cleaned.strip()


def _extract_named_target(text: str) -> str | None:
    patterns = [
        r"(?:节点)?(?:名称为|名为|叫做|名字叫|名字是)\s*(?P<name>.+?)(?:\s*的)?\s*$",
        r"(?:节点)?(?:名称|名字)\s*[:：]\s*(?P<name>.+?)\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip())
        if match:
            return _strip_wrapping_quotes(match.group("name").strip())
    return None


def _strip_wrapping_quotes(text: str) -> str:
    return text.strip().strip("\"'“”‘’`").strip()


def _coerce_value(raw_value: str, old_value: Any, field_name: str) -> Any:
    if field_name == "pidMode":
        mapping = {"正向": "direct", "直接": "direct", "direct": "direct", "反向": "reverse", "reverse": "reverse"}
        return mapping.get(raw_value, raw_value)
    if field_name == "disabledOutValue":
        mapping = {
            "保持": "hold",
            "维持": "hold",
            "hold": "hold",
            "上限": "max",
            "最大": "max",
            "max": "max",
            "下限": "min",
            "最小": "min",
            "min": "min",
            "零": "zero",
            "0": "zero",
            "zero": "zero",
        }
        return mapping.get(raw_value, raw_value)
    if isinstance(old_value, bool):
        return raw_value.lower() in {"true", "1", "yes", "on", "是", "真", "启用", "开启"}
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
