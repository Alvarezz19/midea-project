from __future__ import annotations

import copy
import uuid
from typing import Any

from app.services.json_project import find_nodes, get_tabs, load_project, save_project
from app.services.retrieval import RetrievalError, get_block_by_id
from app.services.schema_library import SchemaLibraryError, generate_nodes_from_schema
from app.services.validator import merge_validation_reports, validate_project, validate_project_change


class PatchEngineError(ValueError):
    """结构化补丁执行失败。"""


PROTECTED_UPDATE_FIELDS = {"id", "type", "z", "wires"}
REPLACE_CONSTANT_FIELDS_BY_TYPE = {
    "constInput": {"fixedValue", "outOfServiceValue"},
    "swInput": {"outOfServiceValue", "swInputDefault"},
    "compare": {"tripPoint"},
    "add": {"fixedValue"},
    "subtract": {"fixedValue"},
    "multiply": {"fixedValue"},
    "divide": {"fixedValue"},
}
DEFAULT_REPLACE_CONSTANT_FIELD_BY_TYPE = {
    "constInput": "fixedValue",
    "swInput": "outOfServiceValue",
    "compare": "tripPoint",
    "add": "fixedValue",
    "subtract": "fixedValue",
    "multiply": "fixedValue",
    "divide": "fixedValue",
}
AUX_DYNAMIC_INPUT_TYPES = {"compare", "limit"}
OPTION_DYNAMIC_INPUT_BASE_COUNTS = {"pid": 3, "fuzzypid": 3}
OPTION_DYNAMIC_INPUT_ALLOWED = {
    "pid": [
        "proportional",
        "integral",
        "derivative",
        "highPidOutLimit",
        "lowPidOutLimit",
        "interval",
        "deadBand",
        "pidMode",
    ],
    "fuzzypid": [
        "highPidOutLimit",
        "lowPidOutLimit",
        "interval",
        "deadBand",
        "pidMode",
        "disabledOutValue",
    ],
}
LINEAR_DYNAMIC_INPUT_TARGETS = {"inputD": 3, "outputD": 5}
HARDWARE_IO_POINT_FIELDS = {"hwExpander", "hwChannelIndex"}
MODBUS_POINT_FIELDS = {
    "modbusPort",
    "modbusTcpIPaddr",
    "modbusTcpPort",
    "modbusAddress",
    "functionCode",
    "modbusRegAddr",
}
LOCAL_BACNET_POINT_FIELDS = {
    "bacnetVisible",
    "bacnetObjectType",
    "bacnetObjectInstanceAuto",
    "bacnetObjectInstance",
    "bacnetObjectPrefix",
    "bacnetObjectDescription",
    "bacnetObjectUnits",
}
BACIP_POINT_FIELDS = {
    "bacnetIpDeviceInstance",
    "bacnetIpObjectType",
    "bacnetIpObjectInstance",
    "bacnetIpPriority",
}
MQTT_POINT_FIELDS = {"topic", "objectName"}
SET_IO_POINT_FIELDS_BY_TYPE = {
    "hwInput": HARDWARE_IO_POINT_FIELDS | LOCAL_BACNET_POINT_FIELDS,
    "hwOutput": HARDWARE_IO_POINT_FIELDS | LOCAL_BACNET_POINT_FIELDS,
    "modbusOutput": MODBUS_POINT_FIELDS | LOCAL_BACNET_POINT_FIELDS,
    "bacipOutput": BACIP_POINT_FIELDS,
    "mqttin": MQTT_POINT_FIELDS,
    "mqttout": MQTT_POINT_FIELDS,
}
SET_IO_POINT_INTEGER_RANGES = {
    "hwExpander": (0, 31),
    "hwChannelIndex": (1, 32),
    "modbusTcpPort": (1, 65535),
    "modbusAddress": (1, 247),
    "functionCode": (1, 127),
    "modbusRegAddr": (0, 65535),
    "bacnetObjectInstance": (0, 4194303),
    "bacnetIpDeviceInstance": (0, 4194303),
    "bacnetIpObjectInstance": (0, 4194303),
    "bacnetIpPriority": (1, 16),
}
SET_IO_POINT_BOOL_FIELDS = {"bacnetVisible", "bacnetObjectInstanceAuto"}
SET_IO_POINT_STRING_FIELDS = {
    "modbusPort",
    "modbusTcpIPaddr",
    "bacnetObjectType",
    "bacnetObjectPrefix",
    "bacnetObjectDescription",
    "bacnetObjectUnits",
    "bacnetIpObjectType",
    "topic",
    "objectName",
}


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
        elif op == "replace_constant":
            changes.extend(_replace_constant(next_nodes, operation, index))
        elif op == "enable_dynamic_input":
            changes.extend(_enable_dynamic_input(next_nodes, operation, index))
        elif op == "set_io_point":
            changes.extend(_set_io_point(next_nodes, operation, index))
        elif op == "rename_node":
            changes.extend(_rename_node(next_nodes, operation, index))
        elif op == "add_comment":
            changes.extend(_add_comment(next_nodes, operation, index))
        elif op == "add_node_from_schema":
            changes.extend(_add_node_from_schema(next_nodes, operation, index))
        elif op == "copy_block":
            changes.extend(_copy_block(next_nodes, operation, index))
        elif op == "connect":
            changes.extend(_connect(next_nodes, operation, index))
        elif op == "disconnect":
            changes.extend(_disconnect(next_nodes, operation, index))
        else:
            raise PatchEngineError(f"不支持的补丁操作: {op}")

    diff = build_node_diff(nodes, next_nodes)
    return {
        "nodes": next_nodes,
        "changed": bool(changes),
        "operation_count": len(operations),
        "changes": changes,
        "diff": diff,
    }


def dry_run_patch(nodes: list[dict[str, Any]], patch: dict[str, Any]) -> dict[str, Any]:
    """执行补丁 dry-run，不产生外部副作用。"""

    result = apply_patch(nodes, patch)
    report = merge_validation_reports(
        validate_project(result["nodes"]),
        validate_project_change(nodes, result["nodes"]),
    )
    return {
        "saved": False,
        "valid": report["valid"],
        "validation_report": report,
        "changed": result["changed"],
        "operation_count": result["operation_count"],
        "changes": result["changes"],
        "diff": result["diff"],
        "nodes": result["nodes"],
    }


def apply_patch_to_project(source_path: str, target_path: str, patch: dict[str, Any]) -> dict[str, Any]:
    """读取工程、应用补丁、校验并保存到目标路径。"""

    source_nodes = load_project(source_path)
    result = apply_patch(source_nodes, patch)
    report = merge_validation_reports(
        validate_project(result["nodes"]),
        validate_project_change(source_nodes, result["nodes"]),
    )
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
        "diff": result["diff"],
        "target_path": target_path,
    }


def dry_run_patch_to_project(source_path: str, patch: dict[str, Any]) -> dict[str, Any]:
    """读取工程并执行 dry-run，不保存工程文件。"""

    return dry_run_patch(load_project(source_path), patch)


def build_node_diff(before_nodes: list[dict[str, Any]], after_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    before_by_id = _node_map(before_nodes)
    after_by_id = _node_map(after_nodes)
    before_ids = set(before_by_id)
    after_ids = set(after_by_id)

    added = [_summarize_diff_node(after_by_id[node_id]) for node_id in sorted(after_ids - before_ids)]
    removed = [_summarize_diff_node(before_by_id[node_id]) for node_id in sorted(before_ids - after_ids)]
    modified: list[dict[str, Any]] = []
    for node_id in sorted(before_ids & after_ids):
        field_changes = _field_changes(before_by_id[node_id], after_by_id[node_id])
        if field_changes:
            modified.append(
                {
                    **_summarize_diff_node(after_by_id[node_id]),
                    "field_changes": field_changes,
                }
            )

    return {
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "modified_count": len(modified),
            "affected_node_count": len(added) + len(removed) + len(modified),
        },
        "affected_node_ids": [item["node_id"] for item in added + removed + modified],
        "added": added,
        "removed": removed,
        "modified": modified,
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


def _node_map(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("id")
        if isinstance(node_id, str):
            result[node_id] = node
    return result


def _summarize_diff_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("id"),
        "type": node.get("type"),
        "name": node.get("name", ""),
        "tab_id": node.get("z"),
    }


def _field_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in sorted(set(before) | set(after)):
        old_value = before.get(field)
        new_value = after.get(field)
        if old_value == new_value:
            continue
        changes.append(
            {
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return changes


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
        if field not in node:
            raise PatchEngineError(f"update_param 不允许新增未知参数: {field}")
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


def _replace_constant(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    selector = operation.get("node_selector")
    if not isinstance(selector, dict):
        raise PatchEngineError("replace_constant 需要 node_selector。")
    if "value" not in operation:
        raise PatchEngineError("replace_constant 需要 value。")

    node = _select_one(nodes, selector, op_index)
    node_type = str(node.get("type", ""))
    allowed_fields = REPLACE_CONSTANT_FIELDS_BY_TYPE.get(node_type)
    if not allowed_fields:
        raise PatchEngineError(f"replace_constant 不支持节点类型: {node_type}")

    field = operation.get("field")
    if field is None:
        field = DEFAULT_REPLACE_CONSTANT_FIELD_BY_TYPE.get(node_type)
    if not isinstance(field, str) or not field:
        raise PatchEngineError("replace_constant 的 field 必须是非空字符串。")
    if field in PROTECTED_UPDATE_FIELDS:
        raise PatchEngineError(f"replace_constant 不允许修改结构字段: {field}")
    if field not in allowed_fields:
        raise PatchEngineError(f"replace_constant 不允许修改 {node_type} 的字段: {field}")
    if field not in node:
        raise PatchEngineError(f"replace_constant 目标节点缺少字段: {field}")

    old_value = node.get(field)
    new_value = operation["value"]
    if old_value == new_value:
        return []
    node[field] = new_value
    return [
        {
            "op": "replace_constant",
            "node_id": node.get("id"),
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
        }
    ]


def _enable_dynamic_input(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    selector = operation.get("node_selector")
    if not isinstance(selector, dict):
        raise PatchEngineError("enable_dynamic_input 需要 node_selector。")

    node = _select_one(nodes, selector, op_index)
    node_type = str(node.get("type", ""))
    if node_type in AUX_DYNAMIC_INPUT_TYPES:
        return _enable_aux_dynamic_input(node)
    if node_type in OPTION_DYNAMIC_INPUT_ALLOWED:
        options = _dynamic_input_options(operation)
        if not options:
            raise PatchEngineError(f"enable_dynamic_input 对 {node_type} 需要 input_option 或 input_options。")
        return _enable_option_dynamic_input(node, options)
    if node_type == "linear":
        options = _dynamic_input_options(operation)
        if not options:
            raise PatchEngineError("enable_dynamic_input 对 linear 需要 input_option 或 input_options。")
        return _enable_linear_dynamic_input(node, options)
    raise PatchEngineError(f"enable_dynamic_input 不支持节点类型: {node_type}")


def _set_io_point(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    selector = operation.get("node_selector")
    params = operation.get("params")
    if not isinstance(selector, dict):
        raise PatchEngineError("set_io_point 需要 node_selector。")
    if not isinstance(params, dict) or not params:
        raise PatchEngineError("set_io_point 需要非空 params。")

    node = _select_one(nodes, selector, op_index)
    node_type = str(node.get("type", ""))
    allowed_fields = SET_IO_POINT_FIELDS_BY_TYPE.get(node_type)
    if not allowed_fields:
        raise PatchEngineError(f"set_io_point 不支持节点类型: {node_type}")

    changes: list[dict[str, Any]] = []
    for field, new_value in params.items():
        if not isinstance(field, str) or not field:
            raise PatchEngineError("set_io_point 的字段名必须是非空字符串。")
        if field in PROTECTED_UPDATE_FIELDS:
            raise PatchEngineError(f"set_io_point 不允许修改结构字段: {field}")
        if field not in allowed_fields:
            raise PatchEngineError(f"set_io_point 不允许修改 {node_type} 的字段: {field}")
        if field not in node:
            raise PatchEngineError(f"set_io_point 目标节点缺少字段: {field}")
        _validate_set_io_point_value(field, new_value)

        old_value = node.get(field)
        if old_value == new_value:
            continue
        node[field] = new_value
        changes.append(
            {
                "op": "set_io_point",
                "node_id": node.get("id"),
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return changes


def _validate_set_io_point_value(field: str, value: Any) -> None:
    if field in SET_IO_POINT_INTEGER_RANGES:
        minimum, maximum = SET_IO_POINT_INTEGER_RANGES[field]
        numeric_value = _coerce_integer(value, field)
        if numeric_value < minimum or numeric_value > maximum:
            raise PatchEngineError(f"set_io_point 的 {field} 必须在 {minimum} 到 {maximum} 之间。")
        return

    if field in SET_IO_POINT_BOOL_FIELDS:
        if not isinstance(value, bool):
            raise PatchEngineError(f"set_io_point 的 {field} 必须是布尔值。")
        return

    if field in SET_IO_POINT_STRING_FIELDS:
        if not isinstance(value, str):
            raise PatchEngineError(f"set_io_point 的 {field} 必须是字符串。")
        if field in {"topic", "objectName", "bacnetObjectType", "bacnetIpObjectType"} and not value.strip():
            raise PatchEngineError(f"set_io_point 的 {field} 不能为空。")
        return

    raise PatchEngineError(f"set_io_point 不支持字段: {field}")


def _coerce_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PatchEngineError(f"set_io_point 的 {field} 必须是整数。")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and text.lstrip("+-").isdigit():
            return int(text)
    raise PatchEngineError(f"set_io_point 的 {field} 必须是整数或整数字符串。")


def _enable_aux_dynamic_input(node: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if node.get("inputAuxEnable") is not True:
        changes.append(_field_change("enable_dynamic_input", node, "inputAuxEnable", True))
        node["inputAuxEnable"] = True
    changes.extend(_set_input_count(node, 2, op="enable_dynamic_input"))
    return changes


def _enable_option_dynamic_input(node: dict[str, Any], options: list[str]) -> list[dict[str, Any]]:
    node_type = str(node.get("type", ""))
    allowed = OPTION_DYNAMIC_INPUT_ALLOWED[node_type]
    existing = node.get("inputsOption")
    if existing is None:
        existing = []
    if not isinstance(existing, list) or any(not isinstance(item, str) for item in existing):
        raise PatchEngineError(f"节点 {node.get('id')} 的 inputsOption 必须是字符串数组。")

    next_options = list(existing)
    for option in options:
        if option not in allowed:
            raise PatchEngineError(f"enable_dynamic_input 不允许 {node_type} 的选项: {option}")
        if option not in next_options:
            next_options.append(option)

    changes: list[dict[str, Any]] = []
    if next_options != existing:
        changes.append(_field_change("enable_dynamic_input", node, "inputsOption", next_options))
        node["inputsOption"] = next_options
    target_inputs = OPTION_DYNAMIC_INPUT_BASE_COUNTS[node_type] + len(next_options)
    changes.extend(_set_input_count(node, target_inputs, op="enable_dynamic_input"))
    return changes


def _enable_linear_dynamic_input(node: dict[str, Any], options: list[str]) -> list[dict[str, Any]]:
    target_inputs = int(node.get("inputs", 1)) if isinstance(node.get("inputs"), int) else 1
    changes: list[dict[str, Any]] = []
    for option in options:
        if option not in LINEAR_DYNAMIC_INPUT_TARGETS:
            raise PatchEngineError(f"enable_dynamic_input 不允许 linear 的选项: {option}")
        if node.get(option) is not True:
            changes.append(_field_change("enable_dynamic_input", node, option, True))
            node[option] = True
        target_inputs = max(target_inputs, LINEAR_DYNAMIC_INPUT_TARGETS[option])
    changes.extend(_set_input_count(node, target_inputs, op="enable_dynamic_input"))
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


def _add_node_from_schema(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    schema_selector = operation.get("schema_selector")
    if schema_selector is None and isinstance(operation.get("module_type"), str):
        schema_selector = {"module_type": operation["module_type"]}
    if not isinstance(schema_selector, dict):
        raise PatchEngineError("add_node_from_schema 需要 schema_selector 或 module_type。")

    try:
        tab_id = _resolve_tab_id(nodes, operation, op_index)
        generated_nodes = generate_nodes_from_schema(
            schema_selector,
            flow_id=tab_id,
            params=operation.get("params") or {},
            x=int(operation.get("x", operation.get("position", {}).get("x", 120))),
            y=int(operation.get("y", operation.get("position", {}).get("y", 80))),
            existing_ids={str(node.get("id")) for node in nodes if isinstance(node.get("id"), str)},
        )
    except SchemaLibraryError as exc:
        raise PatchEngineError(f"第 {op_index} 个操作生成 schema 节点失败: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise PatchEngineError(f"第 {op_index} 个操作的坐标必须是整数。") from exc

    nodes.extend(generated_nodes)
    return [
        {
            "op": "add_node_from_schema",
            "node_id": node["id"],
            "tab_id": tab_id,
            "type": node["type"],
            "name": node.get("name", ""),
        }
        for node in generated_nodes
    ]


def _copy_block(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    block_id = operation.get("block_id") or operation.get("source_block_id")
    if not isinstance(block_id, str) or not block_id.strip():
        raise PatchEngineError("copy_block 需要 block_id。")

    try:
        block = get_block_by_id(block_id)
    except RetrievalError as exc:
        raise PatchEngineError(f"第 {op_index} 个操作指定的功能块不存在: {block_id}") from exc

    source_path = block.get("source_path")
    block_node_ids = block.get("node_ids")
    if not isinstance(source_path, str) or not source_path:
        raise PatchEngineError(f"功能块 {block_id} 缺少 source_path。")
    if not isinstance(block_node_ids, list) or not all(isinstance(node_id, str) for node_id in block_node_ids):
        raise PatchEngineError(f"功能块 {block_id} 缺少有效 node_ids。")
    if not block_node_ids:
        raise PatchEngineError(f"功能块 {block_id} 没有可复制节点。")

    target_tab_id = _resolve_tab_id_for_field(nodes, operation, op_index, field="target_tab_selector", op_name="copy_block")
    try:
        source_nodes = load_project(source_path)
    except ValueError as exc:
        raise PatchEngineError(f"读取功能块源工程失败: {exc}") from exc

    source_by_id = {node.get("id"): node for node in source_nodes if isinstance(node.get("id"), str)}
    missing_ids = [node_id for node_id in block_node_ids if node_id not in source_by_id]
    if missing_ids:
        raise PatchEngineError(f"功能块 {block_id} 的节点在源工程中不存在: {missing_ids[:10]}")

    x_offset = _resolve_int(operation.get("x_offset", operation.get("offset", {}).get("x", 80) if isinstance(operation.get("offset"), dict) else 80), "x_offset")
    y_offset = _resolve_int(operation.get("y_offset", operation.get("offset", {}).get("y", 80) if isinstance(operation.get("offset"), dict) else 80), "y_offset")
    name_prefix = operation.get("name_prefix", "")
    if name_prefix is not None and not isinstance(name_prefix, str):
        raise PatchEngineError("copy_block 的 name_prefix 必须是字符串。")

    existing_ids = {node.get("id") for node in nodes if isinstance(node.get("id"), str)}
    id_mapping: dict[str, str] = {}
    for old_id in block_node_ids:
        new_id = _new_node_id_with_reserved(existing_ids | set(id_mapping.values()))
        id_mapping[old_id] = new_id

    selected_set = set(block_node_ids)
    boundary_preview = _build_copy_block_boundary_preview(
        nodes,
        source_by_id,
        selected_set,
        block,
        id_mapping,
    )
    copied_nodes: list[dict[str, Any]] = []
    dropped_external_input_count = 0
    detached_bacnet_object_count = 0
    for old_id in block_node_ids:
        copied = copy.deepcopy(source_by_id[old_id])
        copied["id"] = id_mapping[old_id]
        copied["z"] = target_tab_id
        if isinstance(copied.get("x"), int):
            copied["x"] = int(copied["x"]) + x_offset
        if isinstance(copied.get("y"), int):
            copied["y"] = int(copied["y"]) + y_offset
        if name_prefix and isinstance(copied.get("name"), str) and copied["name"]:
            copied["name"] = f"{name_prefix}{copied['name']}"

        if _detach_copied_bacnet_object(copied):
            detached_bacnet_object_count += 1

        wires = copied.get("wires")
        if isinstance(wires, list):
            new_wires: list[list[Any]] = []
            for input_sources in wires:
                if not isinstance(input_sources, list):
                    new_wires.append([])
                    continue
                next_sources: list[Any] = []
                for source in input_sources:
                    source_id = _wire_ref_id(source)
                    if source_id in selected_set:
                        next_sources.append(_remap_wire_ref(source, id_mapping[source_id]))
                    else:
                        dropped_external_input_count += 1
                new_wires.append(next_sources)
            copied["wires"] = new_wires
        copied_nodes.append(copied)

    nodes.extend(copied_nodes)
    boundary_connection_changes = _apply_copy_block_boundary_connections(
        nodes,
        operation,
        id_mapping,
        op_index,
    )
    return [
        {
            "op": "copy_block",
            "block_id": block_id,
            "source_path": source_path,
            "target_tab_id": target_tab_id,
            "copied_node_count": len(copied_nodes),
            "id_mapping": id_mapping,
            "dropped_external_input_count": dropped_external_input_count,
            "detached_bacnet_object_count": detached_bacnet_object_count,
            "external_connections": "dropped",
            "boundary_preview": boundary_preview,
            "boundary_connection_count": len(boundary_connection_changes),
            "boundary_connection_changes": boundary_connection_changes,
        }
    ]


def _build_copy_block_boundary_preview(
    target_nodes: list[dict[str, Any]],
    source_by_id: dict[Any, dict[str, Any]],
    selected_set: set[str],
    block: dict[str, Any],
    id_mapping: dict[str, str],
) -> dict[str, Any]:
    target_ids = {str(node.get("id")) for node in target_nodes if isinstance(node.get("id"), str)}
    entry_ports: list[dict[str, Any]] = []
    exit_ports: list[dict[str, Any]] = []
    for target_old_id in sorted(selected_set):
        target_node = source_by_id.get(target_old_id)
        if not isinstance(target_node, dict):
            continue
        wires = target_node.get("wires")
        if not isinstance(wires, list):
            continue
        for target_input, input_sources in enumerate(wires):
            if not isinstance(input_sources, list):
                continue
            for source_ref in input_sources:
                source_old_id = _wire_ref_id(source_ref)
                if not source_old_id or source_old_id in selected_set:
                    continue
                source_node = source_by_id.get(source_old_id)
                source_output = _wire_ref_port(source_ref)
                entry = {
                    "source": _boundary_node_ref(source_node, source_old_id),
                    "source_output": source_output,
                    "target": _boundary_node_ref(target_node, target_old_id),
                    "target_input": target_input,
                    "copied_target_node_id": id_mapping.get(target_old_id),
                    "source_available_in_target_project": source_old_id in target_ids,
                }
                if source_old_id in target_ids and id_mapping.get(target_old_id):
                    entry["suggested_boundary_connection"] = {
                        "role": "entry",
                        "source_node_selector": {"id": source_old_id},
                        "source_output": source_output,
                        "target_copied_from_id": target_old_id,
                        "target_input": target_input,
                    }
                entry_ports.append(entry)

    for downstream_node in source_by_id.values():
        if not isinstance(downstream_node, dict):
            continue
        downstream_old_id = downstream_node.get("id")
        if not isinstance(downstream_old_id, str) or downstream_old_id in selected_set:
            continue
        wires = downstream_node.get("wires")
        if not isinstance(wires, list):
            continue
        for target_input, input_sources in enumerate(wires):
            if not isinstance(input_sources, list):
                continue
            for source_ref in input_sources:
                source_old_id = _wire_ref_id(source_ref)
                if source_old_id not in selected_set:
                    continue
                source_output = _wire_ref_port(source_ref)
                exit_item = {
                    "source": _boundary_node_ref(source_by_id.get(source_old_id), source_old_id),
                    "source_output": source_output,
                    "copied_source_node_id": id_mapping.get(str(source_old_id)),
                    "target": _boundary_node_ref(downstream_node, downstream_old_id),
                    "target_input": target_input,
                    "target_available_in_target_project": downstream_old_id in target_ids,
                }
                if downstream_old_id in target_ids and id_mapping.get(str(source_old_id)):
                    exit_item["suggested_boundary_connection"] = {
                        "role": "exit",
                        "source_copied_from_id": source_old_id,
                        "source_output": source_output,
                        "target_node_selector": {"id": downstream_old_id},
                        "target_input": target_input,
                    }
                exit_ports.append(exit_item)

    return {
        "entry_node_id_mapping": _mapped_ids(block.get("entry_node_ids"), id_mapping),
        "exit_node_id_mapping": _mapped_ids(block.get("exit_node_ids"), id_mapping),
        "entry_ports": entry_ports,
        "exit_ports": exit_ports,
        "connection_policy": "not_connected_by_default",
        "connection_helper": {
            "field": "boundary_connections",
            "entry_required_fields": ["role", "source_node_selector", "source_output", "target_copied_from_id", "target_input"],
            "exit_required_fields": ["role", "source_copied_from_id", "source_output", "target_node_selector", "target_input"],
        },
    }


def _mapped_ids(value: Any, id_mapping: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    return {node_id: id_mapping[node_id] for node_id in value if isinstance(node_id, str) and node_id in id_mapping}


def _boundary_node_ref(node: Any, fallback_id: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {"id": fallback_id}
    return {
        "id": node.get("id", fallback_id),
        "type": node.get("type"),
        "name": node.get("name"),
        "label": node.get("label"),
    }


def _apply_copy_block_boundary_connections(
    nodes: list[dict[str, Any]],
    operation: dict[str, Any],
    id_mapping: dict[str, str],
    op_index: int,
) -> list[dict[str, Any]]:
    raw_connections = operation.get("boundary_connections")
    if raw_connections in (None, []):
        return []
    if not isinstance(raw_connections, list):
        raise PatchEngineError("copy_block.boundary_connections 必须是数组。")

    changes: list[dict[str, Any]] = []
    for connection_index, connection in enumerate(raw_connections):
        if not isinstance(connection, dict):
            raise PatchEngineError(f"copy_block.boundary_connections[{connection_index}] 必须是对象。")
        role = connection.get("role")
        if role == "entry":
            target_old_id = connection.get("target_copied_from_id")
            if not isinstance(target_old_id, str) or target_old_id not in id_mapping:
                raise PatchEngineError(f"copy_block.boundary_connections[{connection_index}] 的 target_copied_from_id 无效。")
            connect_operation = {
                "source_node_selector": _required_selector(connection, "source_node_selector", "copy_block.boundary_connections.entry"),
                "source_output": connection.get("source_output", 0),
                "target_node_selector": {"id": id_mapping[target_old_id]},
                "target_input": connection.get("target_input"),
            }
        elif role == "exit":
            source_old_id = connection.get("source_copied_from_id")
            if not isinstance(source_old_id, str) or source_old_id not in id_mapping:
                raise PatchEngineError(f"copy_block.boundary_connections[{connection_index}] 的 source_copied_from_id 无效。")
            connect_operation = {
                "source_node_selector": {"id": id_mapping[source_old_id]},
                "source_output": connection.get("source_output", 0),
                "target_node_selector": _required_selector(connection, "target_node_selector", "copy_block.boundary_connections.exit"),
                "target_input": connection.get("target_input"),
            }
        else:
            raise PatchEngineError(f"copy_block.boundary_connections[{connection_index}] 的 role 必须是 entry 或 exit。")

        for change in _connect(nodes, connect_operation, op_index):
            next_change = dict(change)
            next_change["op"] = "copy_block_boundary_connect"
            next_change["role"] = role
            next_change["boundary_connection_index"] = connection_index
            changes.append(next_change)
    return changes


def _detach_copied_bacnet_object(node: dict[str, Any]) -> bool:
    if node.get("bacnetVisible") is not True:
        return False
    if str(node.get("bacnetObjectType", "")).strip() == "OBJECT_DISABLED":
        return False

    # 复制功能块时不能隐式复用原工程的 BACnet 对象号，避免导出重复点位。
    node["bacnetVisible"] = False
    node["bacnetObjectType"] = "OBJECT_DISABLED"
    node["bacnetObjectInstanceAuto"] = True
    node["bacnetObjectInstance"] = 1
    node["bacnetObjectPrefix"] = ""
    node["bacnetObjectDescription"] = ""
    return True


def _connect(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    source = _select_one(nodes, _required_selector(operation, "source_node_selector", "connect"), op_index)
    target = _select_one(nodes, _required_selector(operation, "target_node_selector", "connect"), op_index)
    source_id = str(source.get("id"))
    target_id = str(target.get("id"))
    source_output = _resolve_port_index(operation.get("source_output", 0), "source_output")
    target_input = _resolve_port_index(operation.get("target_input", 0), "target_input")

    _assert_port_in_range(source, "outputs", source_output, "source_output")
    _assert_port_in_range(target, "inputs", target_input, "target_input")
    wires = _ensure_input_wires(target)
    link = {"id": source_id, "port": source_output}
    if any(_wire_ref_equals(existing, source_id, source_output) for existing in wires[target_input]):
        return []
    wires[target_input].append(link)
    return [
        {
            "op": "connect",
            "source_node_id": source_id,
            "source_output": source_output,
            "target_node_id": target_id,
            "target_input": target_input,
        }
    ]


def _disconnect(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> list[dict[str, Any]]:
    source_selector = operation.get("source_node_selector")
    target = _select_one(nodes, _required_selector(operation, "target_node_selector", "disconnect"), op_index)
    source: dict[str, Any] | None = None
    source_output = operation.get("source_output")
    if source_selector is not None:
        if not isinstance(source_selector, dict):
            raise PatchEngineError("disconnect 的 source_node_selector 必须是对象。")
        source = _select_one(nodes, source_selector, op_index)
    if source_output is not None:
        source_output = _resolve_port_index(source_output, "source_output")

    target_input = operation.get("target_input")
    wires = _ensure_input_wires(target)
    input_indexes = [_resolve_port_index(target_input, "target_input")] if target_input is not None else list(range(len(wires)))
    removed: list[dict[str, Any]] = []
    source_id = str(source.get("id")) if source else None

    for input_index in input_indexes:
        _assert_port_in_range(target, "inputs", input_index, "target_input")
        kept = []
        for existing in wires[input_index]:
            if source_id is None or _wire_ref_equals(existing, source_id, source_output):
                removed.append(
                    {
                        "op": "disconnect",
                        "source_node_id": _wire_ref_id(existing),
                        "source_output": _wire_ref_port(existing),
                        "target_node_id": target.get("id"),
                        "target_input": input_index,
                    }
                )
            else:
                kept.append(existing)
        wires[input_index] = kept
    return removed


def _resolve_tab_id(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int) -> str:
    return _resolve_tab_id_for_field(nodes, operation, op_index, field="tab_selector", op_name="add_comment")


def _resolve_tab_id_for_field(nodes: list[dict[str, Any]], operation: dict[str, Any], op_index: int, *, field: str, op_name: str) -> str:
    tabs = get_tabs(nodes)
    tab_selector = operation.get(field)
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
    raise PatchEngineError(f"{op_name} 需要 {field}。")


def _required_selector(operation: dict[str, Any], field: str, op_name: str) -> dict[str, Any]:
    selector = operation.get(field)
    if not isinstance(selector, dict):
        raise PatchEngineError(f"{op_name} 需要 {field}。")
    return selector


def _resolve_port_index(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PatchEngineError(f"{field} 必须是非负整数。")
    return value


def _resolve_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PatchEngineError(f"{field} 必须是整数。")
    return value


def _assert_port_in_range(node: dict[str, Any], field: str, index: int, label: str) -> None:
    count = node.get(field)
    if not isinstance(count, int) or count < 0:
        raise PatchEngineError(f"节点 {node.get('id')} 缺少有效 {field}。")
    if index >= count:
        raise PatchEngineError(f"{label} 超出节点 {node.get('id')} 的 {field} 范围。")


def _ensure_input_wires(node: dict[str, Any]) -> list[list[Any]]:
    inputs = node.get("inputs")
    if not isinstance(inputs, int) or inputs < 0:
        raise PatchEngineError(f"节点 {node.get('id')} 缺少有效 inputs。")
    wires = node.get("wires")
    if wires is None:
        wires = []
    if not isinstance(wires, list):
        raise PatchEngineError(f"节点 {node.get('id')} 的 wires 必须是数组。")
    while len(wires) < inputs:
        wires.append([])
    if len(wires) > inputs:
        raise PatchEngineError(f"节点 {node.get('id')} 的 wires 长度大于 inputs。")
    for index, item in enumerate(wires):
        if not isinstance(item, list):
            raise PatchEngineError(f"节点 {node.get('id')} 的 wires[{index}] 必须是数组。")
    node["wires"] = wires
    return wires


def _set_input_count(node: dict[str, Any], target_inputs: int, *, op: str) -> list[dict[str, Any]]:
    if target_inputs < 0:
        raise PatchEngineError("target_inputs 必须是非负整数。")
    current_inputs = node.get("inputs")
    if not isinstance(current_inputs, int) or current_inputs < 0:
        raise PatchEngineError(f"节点 {node.get('id')} 缺少有效 inputs。")
    if target_inputs < current_inputs:
        raise PatchEngineError("enable_dynamic_input 不支持减少输入端口。")

    changes: list[dict[str, Any]] = []
    if current_inputs != target_inputs:
        changes.append(_field_change(op, node, "inputs", target_inputs))
        node["inputs"] = target_inputs
    if "inputsCount" in node and node.get("inputsCount") != target_inputs:
        changes.append(_field_change(op, node, "inputsCount", target_inputs))
        node["inputsCount"] = target_inputs

    before_wires = copy.deepcopy(node.get("wires"))
    wires = _ensure_input_wires(node)
    if before_wires != wires:
        changes.append(
            {
                "op": op,
                "node_id": node.get("id"),
                "field": "wires",
                "old_value": before_wires,
                "new_value": copy.deepcopy(wires),
            }
        )
    return changes


def _dynamic_input_options(operation: dict[str, Any]) -> list[str]:
    raw_options = operation.get("input_options")
    if raw_options is None and "input_option" in operation:
        raw_options = [operation["input_option"]]
    if raw_options is None and "option" in operation:
        raw_options = [operation["option"]]
    if raw_options is None:
        return []
    if not isinstance(raw_options, list) or any(not isinstance(item, str) or not item.strip() for item in raw_options):
        raise PatchEngineError("enable_dynamic_input 的 input_options 必须是非空字符串数组。")
    result: list[str] = []
    for option in raw_options:
        if option not in result:
            result.append(option)
    return result


def _field_change(op: str, node: dict[str, Any], field: str, new_value: Any) -> dict[str, Any]:
    return {
        "op": op,
        "node_id": node.get("id"),
        "field": field,
        "old_value": copy.deepcopy(node.get(field)),
        "new_value": copy.deepcopy(new_value),
    }


def _wire_ref_equals(value: Any, source_id: str, source_output: int | None) -> bool:
    if _wire_ref_id(value) != source_id:
        return False
    if source_output is None:
        return True
    return _wire_ref_port(value) == source_output


def _wire_ref_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _wire_ref_port(value: Any) -> int:
    if isinstance(value, dict) and isinstance(value.get("port"), int):
        return value["port"]
    return 0


def _remap_wire_ref(value: Any, new_source_id: str) -> Any:
    if isinstance(value, str):
        return new_source_id
    if isinstance(value, dict):
        next_value = copy.deepcopy(value)
        next_value["id"] = new_source_id
        return next_value
    return {"id": new_source_id, "port": 0}


def _new_node_id(nodes: list[dict[str, Any]]) -> str:
    existing = {node.get("id") for node in nodes}
    return _new_node_id_with_reserved(existing)


def _new_node_id_with_reserved(existing: set[Any]) -> str:
    while True:
        node_id = uuid.uuid4().hex[:7]
        if node_id not in existing:
            return node_id
