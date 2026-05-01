from __future__ import annotations

import json
from typing import Any

from app.services.json_project import get_tabs


class FlowGraphError(ValueError):
    """局部流程图生成失败。"""


def build_react_flow(
    nodes: list[dict[str, Any]],
    *,
    center_node_id: str | None = None,
    focus_node_ids: list[str] | None = None,
    tab_id: str | None = None,
    max_nodes: int = 80,
    max_edges: int = 160,
    max_chars: int = 120000,
) -> dict[str, Any]:
    """按 input_sources 语义生成预算受控的 React Flow 数据。"""

    if max_nodes < 1 or max_edges < 0 or max_chars < 1000:
        raise FlowGraphError("局部图预算参数无效。")

    node_by_id = {str(node["id"]): node for node in nodes if isinstance(node.get("id"), str)}
    if center_node_id and center_node_id not in node_by_id:
        raise FlowGraphError(f"中心节点不存在: {center_node_id}")
    focus_ids = [node_id for node_id in (focus_node_ids or []) if node_id in node_by_id]
    if center_node_id and center_node_id not in focus_ids:
        focus_ids.insert(0, center_node_id)

    tabs = get_tabs(nodes)
    candidate_ids = _candidate_node_ids(nodes, center_node_id=center_node_id, focus_node_ids=focus_ids, tab_id=tab_id, max_nodes=max_nodes)
    included = set(candidate_ids)
    flow_nodes = [_flow_node(node_by_id[node_id], tabs) for node_id in candidate_ids]
    flow_edges = _flow_edges(nodes, included, max_edges=max_edges)

    result = {
        "nodes": flow_nodes,
        "edges": flow_edges,
        "tabs": [{"id": key, "label": value} for key, value in tabs.items()],
        "budget": {
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "max_chars": max_chars,
            "node_count": len(flow_nodes),
            "edge_count": len(flow_edges),
            "truncated": len(_non_tab_node_ids(nodes)) > len(flow_nodes) or len(flow_edges) >= max_edges,
        },
    }
    while len(json.dumps(result, ensure_ascii=False)) > max_chars and result["nodes"]:
        removed = result["nodes"].pop()
        removed_id = removed["id"]
        result["edges"] = [edge for edge in result["edges"] if edge["source"] != removed_id and edge["target"] != removed_id]
        result["budget"]["node_count"] = len(result["nodes"])
        result["budget"]["edge_count"] = len(result["edges"])
        result["budget"]["truncated"] = True
    return result


def _candidate_node_ids(
    nodes: list[dict[str, Any]],
    *,
    center_node_id: str | None,
    focus_node_ids: list[str],
    tab_id: str | None,
    max_nodes: int,
) -> list[str]:
    all_ids = _non_tab_node_ids(nodes)
    node_by_id = {str(node["id"]): node for node in nodes if isinstance(node.get("id"), str)}
    selected: list[str] = []
    selected_set: set[str] = set()

    def add(node_id: str | None) -> bool:
        if not node_id or node_id in selected_set or node_id not in node_by_id:
            return len(selected) >= max_nodes
        if node_by_id[node_id].get("type") == "tab":
            return len(selected) >= max_nodes
        selected.append(node_id)
        selected_set.add(node_id)
        return len(selected) >= max_nodes

    focus_ids = focus_node_ids or ([center_node_id] if center_node_id else [])
    for node_id in focus_ids:
        if add(node_id):
            return selected

    for node_id in focus_ids:
        if node_id not in node_by_id:
            continue
        for source_id in _input_source_ids(node_by_id[node_id].get("wires")):
            if add(source_id):
                return selected

    focus_set = set(focus_ids)
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or node_id in selected_set or node.get("type") == "tab":
            continue
        if focus_set.intersection(_input_source_ids(node.get("wires"))):
            if add(node_id):
                return selected

    preferred_tab_ids = _preferred_tab_ids(node_by_id, focus_ids, tab_id)
    for node_id in all_ids:
        node = node_by_id[node_id]
        if preferred_tab_ids and node.get("z") not in preferred_tab_ids:
            continue
        if add(node_id):
            return selected

    for node_id in all_ids:
        if add(node_id):
            break
    return selected


def _preferred_tab_ids(node_by_id: dict[str, dict[str, Any]], focus_node_ids: list[str], tab_id: str | None) -> set[str]:
    result = {tab_id} if tab_id else set()
    for node_id in focus_node_ids:
        node = node_by_id.get(node_id)
        node_tab_id = node.get("z") if isinstance(node, dict) else None
        if isinstance(node_tab_id, str) and node_tab_id:
            result.add(node_tab_id)
    return result


def _non_tab_node_ids(nodes: list[dict[str, Any]]) -> list[str]:
    return [str(node["id"]) for node in nodes if isinstance(node.get("id"), str) and node.get("type") != "tab"]


def _flow_node(node: dict[str, Any], tabs: dict[str, str]) -> dict[str, Any]:
    node_id = str(node["id"])
    tab_id = node.get("z")
    return {
        "id": node_id,
        "type": "engineeringNode",
        "position": {"x": _number(node.get("x")), "y": _number(node.get("y"))},
        "data": {
            "node_id": node_id,
            "label": str(node.get("name") or node.get("label") or node_id),
            "module_type": node.get("type"),
            "role": _node_role(str(node.get("type") or ""), str(node.get("name") or "")),
            "tab_id": tab_id,
            "tab_label": tabs.get(tab_id, "") if isinstance(tab_id, str) else "",
            "inputs": node.get("inputs"),
            "outputs": node.get("outputs"),
        },
    }


def _flow_edges(nodes: list[dict[str, Any]], included: set[str], *, max_edges: int) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for target in nodes:
        target_id = target.get("id")
        if not isinstance(target_id, str) or target_id not in included:
            continue
        wires = target.get("wires")
        if not isinstance(wires, list):
            continue
        for target_input, input_sources in enumerate(wires):
            if not isinstance(input_sources, list):
                continue
            for source_index, source in enumerate(input_sources):
                source_id, source_port = _source_ref(source)
                if source_id is None or source_id not in included:
                    continue
                edges.append(
                    {
                        "id": f"edge_{source_id}_{source_port}_{target_id}_{target_input}_{source_index}",
                        "source": source_id,
                        "target": target_id,
                        "sourceHandle": f"out-{source_port}",
                        "targetHandle": f"in-{target_input}",
                        "data": {
                            "source_port": source_port,
                            "target_input": target_input,
                            "semantic": "input_sources",
                        },
                    }
                )
                if len(edges) >= max_edges:
                    return edges
    return edges


def _source_ref(source: Any) -> tuple[str | None, int]:
    if isinstance(source, dict):
        source_id = source.get("id")
        port = source.get("port", 0)
    else:
        source_id = source
        port = 0
    if not isinstance(source_id, str):
        return None, 0
    return source_id, int(port) if isinstance(port, int) else 0


def _input_source_ids(wires: Any) -> list[str]:
    if not isinstance(wires, list):
        return []
    result: list[str] = []
    for input_sources in wires:
        if not isinstance(input_sources, list):
            continue
        for source in input_sources:
            source_id, _ = _source_ref(source)
            if source_id:
                result.append(source_id)
    return result


def _number(value: Any) -> int | float:
    return value if isinstance(value, int | float) else 0


def _node_role(node_type: str, name: str) -> str:
    text = f"{node_type} {name}".lower()
    if "input" in text or node_type in {"swInput", "hwInput"}:
        return "input"
    if "output" in text or node_type in {"swOutput", "hwOutput"}:
        return "output"
    if "bacnet" in text or "modbus" in text or "通讯" in name:
        return "communication"
    if "compare" in text or "比较" in name:
        return "compare"
    if "pid" in text:
        return "pid"
    if any(keyword in text for keyword in ("logic", "and", "or", "not")):
        return "logic"
    if any(keyword in name for keyword in ("保护", "故障", "联锁", "防冻")):
        return "protection"
    if node_type in {"comment", "annotation"}:
        return "note"
    return "unknown"
