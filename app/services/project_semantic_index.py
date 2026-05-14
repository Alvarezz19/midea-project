from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.json_project import get_tabs, load_project, resolve_project_path, summarize_project
from app.services.retrieval import extract_input_source_ids, text_score, tokenize
from app.services.schema_library import load_schema_files


KEY_PARAM_FIELDS = [
    "name",
    "label",
    "info",
    "objectName",
    "topic",
    "fixedValue",
    "outOfServiceValue",
    "tripPoint",
    "as",
    "inputAuxEnable",
    "inputsOption",
    "hwExpander",
    "hwChannelIndex",
    "modbusPort",
    "modbusAddress",
    "modbusRegAddr",
    "bacnetObjectInstance",
]

ROLE_HINTS_BY_TYPE = {
    "compare": ["threshold_compare", "control_logic"],
    "constInput": ["constant", "setting_value"],
    "swInput": ["software_input", "setting_value"],
    "pid": ["pid_control", "control_loop"],
    "fuzzypid": ["pid_control", "control_loop"],
    "hwInput": ["hard_io", "input_point"],
    "hwOutput": ["hard_io", "output_point"],
    "modbusOutput": ["modbus", "communication_point"],
    "bacipOutput": ["bacnet", "communication_point"],
    "mqttin": ["mqtt", "communication_point"],
    "mqttout": ["mqtt", "communication_point"],
}

NODE_TYPE_QUERY_ALIASES = {
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
    "变量模块": "swInput",
    "变量": "swInput",
    "PID控制器": "pid",
    "pid控制器": "pid",
    "PID": "pid",
    "pid": "pid",
    "物理输入": "hwInput",
    "硬接输入": "hwInput",
    "物理输出": "hwOutput",
    "硬接输出": "hwOutput",
    "Modbus": "modbusOutput",
    "modbus": "modbusOutput",
    "BACnet": "bacipOutput",
    "BACIP": "bacipOutput",
    "MQTT": "mqttout",
}


def build_project_semantic_index(project_path: str | Path) -> dict[str, Any]:
    """从当前工程 JSON 构建运行时语义索引。"""

    resolved_path = resolve_project_path(project_path)
    nodes = load_project(resolved_path)
    tabs = get_tabs(nodes)
    node_by_id = {node["id"]: node for node in nodes if isinstance(node.get("id"), str)}
    upstream_by_node, downstream_by_node = _build_edges(node_by_id)
    schema_by_type = _schema_by_module_type()

    tab_counts: dict[str, int] = {tab_id: 0 for tab_id in tabs}
    for node in nodes:
        tab_id = node.get("z")
        if isinstance(tab_id, str) and tab_id in tab_counts:
            tab_counts[tab_id] += 1

    semantic_nodes: list[dict[str, Any]] = []
    by_tab: dict[str, list[str]] = {}
    by_type: dict[str, list[str]] = {}
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        node_type = str(node.get("type", ""))
        tab_id = node_id if node_type == "tab" else node.get("z")
        tab_label = tabs.get(tab_id, "") if isinstance(tab_id, str) else ""
        schema = schema_by_type.get(node_type)
        upstream_ids = sorted(upstream_by_node.get(node_id, set()))
        downstream_ids = sorted(downstream_by_node.get(node_id, set()))
        upstream_labels = [_node_display_text(node_by_id[source_id], tabs) for source_id in upstream_ids[:8]]
        downstream_labels = [_node_display_text(node_by_id[target_id], tabs) for target_id in downstream_ids[:8]]
        key_params = _key_params(node)
        semantic_text = _semantic_text(
            node=node,
            tab_label=tab_label,
            key_params=key_params,
            schema=schema,
            upstream_labels=upstream_labels,
            downstream_labels=downstream_labels,
        )
        item = {
            "id": node_id,
            "type": node_type,
            "tab_id": tab_id,
            "tab_label": tab_label,
            "name": str(node.get("name", "")),
            "label": str(node.get("label", "")),
            "semantic_text": semantic_text,
            "key_params": key_params,
            "inputs": node.get("inputs", 0),
            "outputs": node.get("outputs", 0),
            "input_sources": upstream_ids,
            "upstream_labels": upstream_labels,
            "downstream_labels": downstream_labels,
            "schema_path": schema.get("path") if schema else None,
            "schema_category": schema.get("category") if schema else None,
            "schema_name": schema.get("name") if schema else None,
            "schema_module_type": schema.get("module_type") if schema else None,
            "schema_parameter_fields": schema.get("parameter_fields", []) if schema else [],
            "role_hints": ROLE_HINTS_BY_TYPE.get(node_type, []),
        }
        semantic_nodes.append(item)
        if isinstance(tab_id, str):
            by_tab.setdefault(tab_id, []).append(node_id)
        by_type.setdefault(node_type, []).append(node_id)

    return {
        "project_path": str(resolved_path),
        "summary": summarize_project(nodes),
        "tabs": [{"id": tab_id, "label": label, "node_count": tab_counts.get(tab_id, 0)} for tab_id, label in tabs.items()],
        "nodes": semantic_nodes,
        "by_tab": by_tab,
        "by_type": by_type,
    }


def search_current_project_nodes(
    query: str,
    *,
    project_path: str | Path,
    tab_label_contains: str | None = None,
    node_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """检索当前版本节点语义索引，返回预算内候选。"""

    index = build_project_semantic_index(project_path)
    query_tokens = tokenize(query)
    type_alias = _node_type_from_query(query)
    results: list[dict[str, Any]] = []
    for node in index["nodes"]:
        if tab_label_contains and tab_label_contains not in str(node.get("tab_label", "")):
            continue
        if node_type and node.get("type") != node_type:
            continue
        score = text_score(str(node.get("semantic_text", "")), query_tokens)
        if type_alias and node.get("type") == type_alias:
            score += 2.0
        if tab_label_contains and tab_label_contains in str(node.get("tab_label", "")):
            score += 1.0
        if node_type and node.get("type") == node_type:
            score += 0.5
        if score <= 0:
            continue
        item = _public_node_candidate(node)
        item["score"] = round(score, 4)
        results.append(item)
    results.sort(key=lambda item: (item["score"], len(item.get("input_sources") or [])), reverse=True)
    return results[: max(0, limit)]


def summarize_current_project_for_planner(
    project_path: str | Path,
    *,
    max_nodes: int = 120,
    max_chars: int = 24000,
) -> dict[str, Any]:
    """生成可放入 planner prompt 的当前工程摘要。"""

    index = build_project_semantic_index(project_path)
    selected_nodes: list[dict[str, Any]] = []
    used_chars = 0
    for node in index["nodes"]:
        item = _public_node_candidate(node)
        estimated = len(json.dumps(item, ensure_ascii=False))
        if len(selected_nodes) >= max_nodes or used_chars + estimated > max_chars:
            break
        selected_nodes.append(item)
        used_chars += estimated
    return {
        "project_path": index["project_path"],
        "summary": index["summary"],
        "tabs": index["tabs"],
        "nodes": selected_nodes,
        "budget": {
            "max_nodes": max_nodes,
            "max_chars": max_chars,
            "returned_nodes": len(selected_nodes),
            "estimated_chars": used_chars,
            "truncated": len(selected_nodes) < len(index["nodes"]),
        },
    }


def summarize_affected_nodes(project_path: str | Path, affected_node_ids: list[str], *, limit: int = 12) -> list[dict[str, Any]]:
    """把变更影响节点转换成用户可读实体摘要。"""

    if not affected_node_ids:
        return []
    index = build_project_semantic_index(project_path)
    by_id = {node["id"]: node for node in index["nodes"]}
    result: list[dict[str, Any]] = []
    for node_id in affected_node_ids[:limit]:
        node = by_id.get(node_id)
        if not node:
            continue
        result.append(
            {
                "kind": "node",
                "id": node["id"],
                "selector": {"id": node["id"]},
                "display_name": _candidate_display_name(node),
                "description": _candidate_description(node),
                "tab_id": node.get("tab_id"),
                "tab_label": node.get("tab_label"),
                "type": node.get("type"),
                "name": node.get("name"),
                "label": node.get("label"),
                "key_params": node.get("key_params", {}),
            }
        )
    return result


def _build_edges(node_by_id: dict[str, dict[str, Any]]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    upstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    downstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for node_id, node in node_by_id.items():
        for source_id in extract_input_source_ids(node.get("wires")):
            if source_id in node_by_id:
                upstream_by_node[node_id].add(source_id)
                downstream_by_node[source_id].add(node_id)
    return upstream_by_node, downstream_by_node


@lru_cache(maxsize=1)
def _schema_by_module_type() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in load_schema_files():
        schema = item["schema"]
        module_type = schema.get("module_type")
        if not isinstance(module_type, str) or module_type in result:
            continue
        params = schema.get("parameters_schema") if isinstance(schema.get("parameters_schema"), dict) else {}
        result[module_type] = {
            "path": item["path"],
            "module_type": module_type,
            "category": schema.get("category"),
            "name": schema.get("name"),
            "description": schema.get("description"),
            "keywords": schema.get("keywords") if isinstance(schema.get("keywords"), list) else [],
            "parameter_fields": list(params.keys()),
        }
    return result


def _key_params(node: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in KEY_PARAM_FIELDS:
        if field not in node:
            continue
        value = node.get(field)
        if isinstance(value, (dict, list)):
            if value:
                result[field] = value
        elif value not in (None, ""):
            result[field] = value
    return result


def _semantic_text(
    *,
    node: dict[str, Any],
    tab_label: str,
    key_params: dict[str, Any],
    schema: dict[str, Any] | None,
    upstream_labels: list[str],
    downstream_labels: list[str],
) -> str:
    parts = [
        tab_label,
        str(node.get("type", "")),
        str(node.get("name", "")),
        str(node.get("label", "")),
        str(node.get("info", "")),
        json.dumps(key_params, ensure_ascii=False),
        " ".join(upstream_labels),
        " ".join(downstream_labels),
    ]
    if schema:
        parts.extend(
            [
                str(schema.get("name", "")),
                str(schema.get("category", "")),
                str(schema.get("description", "")),
                " ".join(str(keyword) for keyword in schema.get("keywords", [])),
                " ".join(str(field) for field in schema.get("parameter_fields", [])),
            ]
        )
    return " ".join(part for part in parts if part)


def _node_display_text(node: dict[str, Any], tabs: dict[str, str]) -> str:
    tab_id = node.get("z")
    tab_label = tabs.get(tab_id, "") if isinstance(tab_id, str) else ""
    name = node.get("name") or node.get("label") or node.get("id")
    return " / ".join(part for part in [tab_label, str(name), str(node.get("type", ""))] if part)


def _public_node_candidate(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "node",
        "node_id": node.get("id"),
        "id": node.get("id"),
        "selector": {"id": node.get("id")},
        "tab_id": node.get("tab_id"),
        "tab_label": node.get("tab_label"),
        "type": node.get("type"),
        "name": node.get("name"),
        "label": node.get("label"),
        "display_name": _candidate_display_name(node),
        "description": _candidate_description(node),
        "key_params": node.get("key_params", {}),
        "input_sources": node.get("input_sources", []),
        "upstream_labels": node.get("upstream_labels", []),
        "downstream_labels": node.get("downstream_labels", []),
        "schema_path": node.get("schema_path"),
        "schema_name": node.get("schema_name"),
        "schema_module_type": node.get("schema_module_type"),
        "role_hints": node.get("role_hints", []),
    }


def _candidate_display_name(node: dict[str, Any]) -> str:
    name = str(node.get("name") or node.get("label") or "").strip()
    parts = [str(node.get("tab_label") or "").strip()]
    if name:
        parts.append(name)
    parts.append(str(node.get("schema_name") or node.get("type") or "").strip())
    return " / ".join(part for part in parts if part)


def _candidate_description(node: dict[str, Any]) -> str:
    chunks: list[str] = []
    key_params = node.get("key_params") if isinstance(node.get("key_params"), dict) else {}
    for field in ("tripPoint", "fixedValue", "outOfServiceValue", "as", "inputAuxEnable"):
        if field in key_params:
            chunks.append(f"{field}={key_params[field]}")
    upstream = node.get("upstream_labels") or []
    downstream = node.get("downstream_labels") or []
    if upstream:
        chunks.append("上游：" + "、".join(str(item) for item in upstream[:3]))
    if downstream:
        chunks.append("下游：" + "、".join(str(item) for item in downstream[:3]))
    return "；".join(chunks)


def _node_type_from_query(query: str) -> str | None:
    for keyword, node_type in NODE_TYPE_QUERY_ALIASES.items():
        if keyword in query:
            return node_type
    return None
