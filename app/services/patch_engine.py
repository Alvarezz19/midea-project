from __future__ import annotations

import copy
import uuid
from typing import Any

from app.services.json_project import find_nodes, get_tabs, load_project, save_project
from app.services.validator import validate_project


class PatchEngineError(ValueError):
    """结构化补丁执行失败。"""


PROTECTED_UPDATE_FIELDS = {"id", "type", "z", "wires"}


def apply_patch(nodes: list[dict[str, Any]], patch: dict[str, Any]) -> dict[str, Any]:
    """对工程节点数组应用结构化补丁，返回新节点和变更报告。"""

    operations = _normalize_operations(patch)
    next_nodes = copy.deepcopy(nodes)
    changes: list[dict[str, Any]] = []

    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise PatchEngineError(f"第 {index} 个补丁操作必须是对象。")
        op = operation.get("op")
        if op == "update_param":
            changes.extend(_update_param(next_nodes, operation, index))
        elif op == "rename_node":
            changes.extend(_rename_node(next_nodes, operation, index))
        elif op == "add_comment":
            changes.extend(_add_comment(next_nodes, operation, index))
        else:
            raise PatchEngineError(f"不支持的补丁操作: {op}")

    return {
        "nodes": next_nodes,
        "changed": bool(changes),
        "operation_count": len(operations),
        "changes": changes,
    }


def apply_patch_to_project(source_path: str, target_path: str, patch: dict[str, Any]) -> dict[str, Any]:
    """读取工程、应用补丁、校验并保存到目标路径。"""

    source_nodes = load_project(source_path)
    result = apply_patch(source_nodes, patch)
    report = validate_project(result["nodes"])
    if not report["valid"]:
        return {
            "saved": False,
            "validation_report": report,
            "changes": result["changes"],
        }
    save_project(target_path, result["nodes"])
    return {
        "saved": True,
        "validation_report": report,
        "changes": result["changes"],
        "target_path": target_path,
    }


def _normalize_operations(patch: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(patch, dict):
        raise PatchEngineError("补丁必须是对象。")
    if "operations" in patch:
        operations = patch["operations"]
        if not isinstance(operations, list):
            raise PatchEngineError("operations 必须是数组。")
        return operations
    if "op" in patch:
        return [patch]
    raise PatchEngineError("补丁必须包含 op 或 operations。")


def _select_one(nodes: list[dict[str, Any]], selector: dict[str, Any], op_index: int) -> dict[str, Any]:
    matches = find_nodes(nodes, selector)
    if not matches:
        raise PatchEngineError(f"第 {op_index} 个操作未匹配到节点，selector={selector}")
    if len(matches) > 1:
        sample_ids = [str(node.get("id")) for node in matches[:10]]
        raise PatchEngineError(f"第 {op_index} 个操作匹配到 {len(matches)} 个节点，必须唯一。样例 id={sample_ids}")
    return matches[0]


def _update_param(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    selector = operation.get("node_selector")
    params = operation.get("params")
    if not isinstance(selector, dict):
        raise PatchEngineError("update_param 需要 node_selector。")
    if not isinstance(params, dict) or not params:
        raise PatchEngineError("update_param 需要非空 params。")

    node = _select_one(nodes, selector, op_index)
    node_id = str(node.get("id"))
    changes: list[dict[str, Any]] = []
    for field, new_value in params.items():
        if field in PROTECTED_UPDATE_FIELDS:
            raise PatchEngineError(f"update_param 不允许修改结构字段: {field}")
        old_value = node.get(field)
        if old_value == new_value:
            continue
        node[field] = new_value
        changes.append(
            {
                "op": "update_param",
                "node_id": node_id,
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return changes


def _rename_node(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    selector = operation.get("node_selector")
    new_name = operation.get("new_name")
    if not isinstance(selector, dict):
        raise PatchEngineError("rename_node 需要 node_selector。")
    if not isinstance(new_name, str) or not new_name.strip():
        raise PatchEngineError("rename_node 需要非空 new_name。")

    node = _select_one(nodes, selector, op_index)
    old_name = node.get("name")
    if old_name == new_name:
        return []
    node["name"] = new_name
    return [
        {
            "op": "rename_node",
            "node_id": node.get("id"),
            "field": "name",
            "old_value": old_name,
            "new_value": new_name,
        }
    ]


def _add_comment(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    text = operation.get("text")
    if not isinstance(text, str) or not text.strip():
        raise PatchEngineError("add_comment 需要非空 text。")

    tab_id = _resolve_tab_id(nodes, operation, op_index)
    comment_id = _new_node_id(nodes)
    comment = {
        "id": comment_id,
        "type": "comment",
        "z": tab_id,
        "name": text,
        "info": operation.get("info", ""),
        "x": int(operation.get("x", 120)),
        "y": int(operation.get("y", 80)),
        "inputs": 0,
        "outputs": 0,
        "wires": [],
    }
    nodes.append(comment)
    return [
        {
            "op": "add_comment",
            "node_id": comment_id,
            "tab_id": tab_id,
            "text": text,
        }
    ]


def _resolve_tab_id(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> str:
    tabs = get_tabs(nodes)
    tab_selector = operation.get("tab_selector")
    if isinstance(tab_selector, dict):
        if isinstance(tab_selector.get("id"), str):
            tab_id = tab_selector["id"]
            if tab_id in tabs:
                return tab_id
            raise PatchEngineError(f"第 {op_index} 个操作指定的 tab id 不存在: {tab_id}")
        if isinstance(tab_selector.get("label"), str):
            matched = [tab_id for tab_id, label in tabs.items() if label == tab_selector["label"]]
            if len(matched) == 1:
                return matched[0]
            if not matched:
                raise PatchEngineError(f"第 {op_index} 个操作指定的 tab label 不存在: {tab_selector['label']}")
            raise PatchEngineError(f"第 {op_index} 个操作指定的 tab label 不唯一: {tab_selector['label']}")
        if isinstance(tab_selector.get("label_contains"), str):
            keyword = tab_selector["label_contains"]
            matched = [tab_id for tab_id, label in tabs.items() if keyword in label]
            if len(matched) == 1:
                return matched[0]
            if not matched:
                raise PatchEngineError(f"第 {op_index} 个操作未匹配到 tab: {keyword}")
            raise PatchEngineError(f"第 {op_index} 个操作匹配到多个 tab: {keyword}")

    if len(tabs) == 1:
        return next(iter(tabs))
    raise PatchEngineError("add_comment 需要 tab_selector。")


def _new_node_id(nodes: list[dict[str, Any]]) -> str:
    existing = {node.get("id") for node in nodes}
    while True:
        node_id = uuid.uuid4().hex[:7]
        if node_id not in existing:
            return node_id
