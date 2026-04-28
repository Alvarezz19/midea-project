from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Literal

from app.services.schema_library import load_schema_files


Severity = Literal["error", "warning"]
AUX_DYNAMIC_INPUT_TYPES = {"compare", "limit"}
OPTION_DYNAMIC_INPUT_BASE_COUNTS = {"pid": 3, "fuzzypid": 3}
OPTION_DYNAMIC_INPUT_ALLOWED = {
    "pid": {
        "proportional",
        "integral",
        "derivative",
        "highPidOutLimit",
        "lowPidOutLimit",
        "interval",
        "deadBand",
        "pidMode",
    },
    "fuzzypid": {
        "highPidOutLimit",
        "lowPidOutLimit",
        "interval",
        "deadBand",
        "pidMode",
        "disabledOutValue",
    },
}
COMMON_NODE_FIELDS = {"id", "z", "type", "name", "label", "info", "x", "y", "wires", "inputs", "outputs"}


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    node_id: str | None = None
    path: str | None = None


def _issue(
    issues: list[ValidationIssue],
    severity: Severity,
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    path: str | None = None,
) -> None:
    issues.append(ValidationIssue(severity=severity, code=code, message=message, node_id=node_id, path=path))


def validate_project(nodes: Any) -> dict[str, Any]:
    """校验工程 JSON 的基础结构和拓扑引用。"""

    issues: list[ValidationIssue] = []
    if not isinstance(nodes, list):
        _issue(issues, "error", "root_not_array", "工程 JSON 根节点必须是数组。")
        return _build_report(issues)

    id_counter: Counter[str] = Counter()
    node_ids: set[str] = set()
    tab_ids: set[str] = set()
    subflow_ids: set[str] = set()

    for index, node in enumerate(nodes):
        path = f"$[{index}]"
        if not isinstance(node, dict):
            _issue(issues, "error", "node_not_object", "工程数组项必须是对象。", path=path)
            continue

        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not node_id.strip():
            _issue(issues, "error", "missing_id", "节点缺少有效 id。", path=path)
        else:
            id_counter[node_id] += 1
            node_ids.add(node_id)

        if not isinstance(node_type, str) or not node_type.strip():
            _issue(issues, "error", "missing_type", "节点缺少有效 type。", node_id=node_id if isinstance(node_id, str) else None, path=path)
            continue

        if node_type == "tab" and isinstance(node_id, str):
            tab_ids.add(node_id)
        elif node_type == "subflow" and isinstance(node_id, str):
            subflow_ids.add(node_id)

    for node_id, count in id_counter.items():
        if count > 1:
            _issue(issues, "error", "duplicate_id", f"节点 id 重复 {count} 次。", node_id=node_id)

    flow_scope_ids = tab_ids | subflow_ids

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        path = f"$[{index}]"
        node_id = node.get("id") if isinstance(node.get("id"), str) else None
        node_type = node.get("type")

        _validate_port_count(node, "inputs", issues, node_id=node_id, path=path)
        _validate_port_count(node, "outputs", issues, node_id=node_id, path=path)
        _validate_wires(node, node_ids, issues, node_id=node_id, path=path)
        _validate_dynamic_ports(node, issues, node_id=node_id, path=path)
        _validate_schema_parameters(node, issues, node_id=node_id, path=path)

        z_value = node.get("z")
        if isinstance(node_type, str) and node_type not in {"tab", "subflow"}:
            if isinstance(z_value, str) and z_value and z_value not in flow_scope_ids:
                _issue(
                    issues,
                    "warning",
                    "unknown_flow_scope",
                    "节点 z 字段未引用已知 tab 或 subflow。",
                    node_id=node_id,
                    path=f"{path}.z",
                )

    _validate_io_communication_conflicts(nodes, issues)

    return _build_report(issues)


def _validate_port_count(
    node: dict[str, Any],
    field: str,
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    if field not in node:
        return
    value = node[field]
    if not isinstance(value, int) or value < 0:
        _issue(
            issues,
            "error",
            f"invalid_{field}",
            f"节点 {field} 必须是非负整数。",
            node_id=node_id,
            path=f"{path}.{field}",
        )


def _validate_wires(
    node: dict[str, Any],
    node_ids: set[str],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    wires = node.get("wires")
    if wires is None:
        return
    if not isinstance(wires, list):
        _issue(issues, "error", "invalid_wires", "节点 wires 必须是数组。", node_id=node_id, path=f"{path}.wires")
        return

    for input_index, input_sources in enumerate(wires):
        source_path = f"{path}.wires[{input_index}]"
        if not isinstance(input_sources, list):
            _issue(issues, "error", "invalid_wire_input", "wires 的每个输入端口必须是上游源 id 数组。", node_id=node_id, path=source_path)
            continue
        for source_index, source_id in enumerate(input_sources):
            if isinstance(source_id, dict):
                source_ref = source_id.get("id")
            else:
                source_ref = source_id

            if not isinstance(source_ref, str) or not source_ref.strip():
                _issue(
                    issues,
                    "error",
                    "invalid_wire_source",
                    "上游源引用必须是非空字符串 id，或包含 id 字段的对象。",
                    node_id=node_id,
                    path=f"{source_path}[{source_index}]",
                )
            elif source_ref not in node_ids:
                _issue(
                    issues,
                    "error",
                    "missing_wire_source",
                    "上游源节点不存在。",
                    node_id=node_id,
                    path=f"{source_path}[{source_index}]",
                )

    inputs = node.get("inputs")
    if isinstance(inputs, int) and inputs >= 0 and len(wires) > inputs:
        _issue(
            issues,
            "error",
            "wires_inputs_mismatch",
            "wires 数组长度不应大于 inputs 输入端口数量。",
            node_id=node_id,
            path=f"{path}.wires",
        )


def _validate_dynamic_ports(
    node: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    node_type = node.get("type")
    if not isinstance(node_type, str):
        return

    expected_inputs = _expected_dynamic_inputs(node, issues, node_id=node_id, path=path)
    if expected_inputs is None:
        return

    inputs = node.get("inputs")
    if isinstance(inputs, int) and inputs >= 0 and inputs != expected_inputs:
        _issue(
            issues,
            "error",
            "dynamic_inputs_mismatch",
            f"动态端口配置要求 inputs 为 {expected_inputs}。",
            node_id=node_id,
            path=f"{path}.inputs",
        )

    inputs_count = node.get("inputsCount")
    if inputs_count is not None and inputs_count != expected_inputs:
        _issue(
            issues,
            "error",
            "dynamic_inputs_count_mismatch",
            f"inputsCount 应与动态端口配置一致，当前应为 {expected_inputs}。",
            node_id=node_id,
            path=f"{path}.inputsCount",
        )

    wires = node.get("wires")
    if isinstance(wires, list) and len(wires) != expected_inputs:
        _issue(
            issues,
            "error",
            "dynamic_wires_inputs_mismatch",
            f"动态端口配置要求 wires 长度为 {expected_inputs}。",
            node_id=node_id,
            path=f"{path}.wires",
        )


def _expected_dynamic_inputs(
    node: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> int | None:
    node_type = node.get("type")
    if node_type in AUX_DYNAMIC_INPUT_TYPES:
        input_aux_enable = node.get("inputAuxEnable")
        if not isinstance(input_aux_enable, bool):
            _issue(
                issues,
                "error",
                "invalid_input_aux_enable",
                "inputAuxEnable 必须是布尔值。",
                node_id=node_id,
                path=f"{path}.inputAuxEnable",
            )
            return None
        return 2 if input_aux_enable else 1

    if node_type in OPTION_DYNAMIC_INPUT_BASE_COUNTS:
        options = _validate_inputs_option(node, issues, node_id=node_id, path=path)
        if options is None:
            return None
        return OPTION_DYNAMIC_INPUT_BASE_COUNTS[node_type] + len(options)

    if node_type == "linear":
        input_dynamic = _validate_bool_field(node, "inputD", issues, node_id=node_id, path=path)
        output_dynamic = _validate_bool_field(node, "outputD", issues, node_id=node_id, path=path)
        if input_dynamic is None or output_dynamic is None:
            return None
        expected = 1
        if input_dynamic:
            expected = max(expected, 3)
        if output_dynamic:
            expected = max(expected, 5)
        return expected

    return None


def _validate_inputs_option(
    node: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> list[str] | None:
    node_type = str(node.get("type"))
    options = node.get("inputsOption")
    if not isinstance(options, list) or any(not isinstance(item, str) for item in options):
        _issue(
            issues,
            "error",
            "invalid_inputs_option",
            "inputsOption 必须是字符串数组。",
            node_id=node_id,
            path=f"{path}.inputsOption",
        )
        return None

    allowed = OPTION_DYNAMIC_INPUT_ALLOWED[node_type]
    seen: set[str] = set()
    for index, option in enumerate(options):
        if option in seen:
            _issue(
                issues,
                "error",
                "duplicate_inputs_option",
                "inputsOption 中不能包含重复选项。",
                node_id=node_id,
                path=f"{path}.inputsOption[{index}]",
            )
        seen.add(option)
        if option not in allowed:
            _issue(
                issues,
                "error",
                "invalid_inputs_option",
                f"{node_type} 不支持动态输入选项 {option}。",
                node_id=node_id,
                path=f"{path}.inputsOption[{index}]",
            )
    return options


def _validate_bool_field(
    node: dict[str, Any],
    field: str,
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> bool | None:
    value = node.get(field)
    if isinstance(value, bool):
        return value
    _issue(
        issues,
        "error",
        f"invalid_{field}",
        f"{field} 必须是布尔值。",
        node_id=node_id,
        path=f"{path}.{field}",
    )
    return None


def _validate_schema_parameters(
    node: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    node_type = node.get("type")
    if not isinstance(node_type, str):
        return

    schema = _schema_index().get(node_type)
    if schema is None:
        return

    parameters_schema = schema.get("parameters_schema") or {}
    if not isinstance(parameters_schema, dict):
        return

    allowed_fields = set(schema.get("__template_fields__", set())) | COMMON_NODE_FIELDS
    for field in sorted(set(node) - allowed_fields):
        _issue(
            issues,
            "error",
            "schema_unknown_field",
            f"{node_type} 节点包含 schema 未定义字段 {field}。",
            node_id=node_id,
            path=f"{path}.{field}",
        )

    for field, raw_definition in parameters_schema.items():
        definition = raw_definition if isinstance(raw_definition, dict) else {}
        if field not in node:
            if "default" not in definition:
                _issue(
                    issues,
                    "error",
                    "schema_missing_required_param",
                    f"{node_type} 节点缺少必填参数 {field}。",
                    node_id=node_id,
                    path=f"{path}.{field}",
                )
            continue

        value = node[field]
        coerced, type_error = _coerce_schema_value(value, definition)
        if type_error:
            _issue(
                issues,
                "error",
                "schema_invalid_type",
                f"{node_type}.{field} 类型不符合 schema：{type_error}",
                node_id=node_id,
                path=f"{path}.{field}",
            )
            continue

        _validate_schema_enum(node_type, field, coerced, definition, issues, node_id=node_id, path=path)
        _validate_schema_range(node_type, field, coerced, definition, issues, node_id=node_id, path=path)


@lru_cache(maxsize=1)
def _schema_index() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for item in load_schema_files():
        schema = dict(item["schema"])
        module_type = schema.get("module_type")
        if not isinstance(module_type, str) or not module_type:
            continue
        schema["__schema_path__"] = item["path"]
        schema["__template_fields__"] = _collect_template_fields(schema.get("template_json"))
        schemas[module_type] = schema
    return schemas


def _collect_template_fields(template: Any) -> set[str]:
    template_items = template if isinstance(template, list) else [template]
    fields: set[str] = set()
    for item in template_items:
        if isinstance(item, dict):
            fields.update(str(key) for key in item)
    return fields


def _coerce_schema_value(value: Any, definition: dict[str, Any]) -> tuple[Any, str | None]:
    expected_type = str(definition.get("type", "")).lower().strip()
    if expected_type in {"integer", "int", "non_negative_integer", "unsigned integer", "整数"}:
        return _coerce_int(value)
    if expected_type in {"number", "double", "float"}:
        return _coerce_number(value)
    if expected_type == "boolean":
        if isinstance(value, bool):
            return value, None
        return value, "必须是布尔值"
    if expected_type == "string":
        if isinstance(value, str):
            return value, None
        return value, "必须是字符串"
    if "array" in expected_type:
        if not isinstance(value, list):
            return value, "必须是数组"
        if "string" in expected_type and any(not isinstance(item, str) for item in value):
            return value, "必须是字符串数组"
        return value, None
    return value, None


def _coerce_int(value: Any) -> tuple[int | Any, str | None]:
    if isinstance(value, bool):
        return value, "必须是整数"
    if isinstance(value, int):
        return value, None
    if isinstance(value, str):
        text = value.strip()
        if text and text.lstrip("+-").isdigit():
            return int(text), None
    return value, "必须是整数"


def _coerce_number(value: Any) -> tuple[int | float | Any, str | None]:
    if isinstance(value, bool):
        return value, "必须是数字"
    if isinstance(value, (int, float)):
        return value, None
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return float(text), None
            except ValueError:
                pass
    return value, "必须是数字"


def _validate_schema_enum(
    node_type: str,
    field: str,
    value: Any,
    definition: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    enum = definition.get("enum")
    if not isinstance(enum, list) or not enum:
        return

    if isinstance(value, list):
        invalid_items = [item for item in value if item not in enum]
        if invalid_items:
            _issue(
                issues,
                "error",
                "schema_invalid_enum",
                f"{node_type}.{field} 包含非法枚举项 {invalid_items}，允许值为 {enum}。",
                node_id=node_id,
                path=f"{path}.{field}",
            )
    elif value not in enum:
        _issue(
            issues,
            "error",
            "schema_invalid_enum",
            f"{node_type}.{field} 必须是枚举值之一：{enum}。",
            node_id=node_id,
            path=f"{path}.{field}",
        )


def _validate_schema_range(
    node_type: str,
    field: str,
    value: Any,
    definition: dict[str, Any],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return

    minimum = definition.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        _issue(
            issues,
            "error",
            "schema_below_minimum",
            f"{node_type}.{field} 小于 schema 最小值 {minimum}。",
            node_id=node_id,
            path=f"{path}.{field}",
        )

    maximum = definition.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        _issue(
            issues,
            "error",
            "schema_above_maximum",
            f"{node_type}.{field} 大于 schema 最大值 {maximum}。",
            node_id=node_id,
            path=f"{path}.{field}",
        )


def _validate_io_communication_conflicts(nodes: list[Any], issues: list[ValidationIssue]) -> None:
    conflict_groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            continue

        node_type = node.get("type")
        if node_type in {"hwInput", "hwOutput"}:
            _add_conflict_key(
                conflict_groups,
                "hardware_io_channel_conflict",
                (_normal_key(node.get("hwExpander")), _normal_key(node.get("hwChannelIndex"))),
                node_id,
            )

        if node_type == "modbusOutput":
            _add_conflict_key(
                conflict_groups,
                "modbus_point_conflict",
                (
                    _normal_key(node.get("modbusPort")),
                    _normal_key(node.get("modbusTcpIPaddr")) or "rtu",
                    _normal_key(node.get("modbusTcpPort")),
                    _normal_key(node.get("modbusAddress")),
                    _normal_key(node.get("functionCode")),
                    _normal_key(node.get("modbusRegAddr")),
                ),
                node_id,
            )

        if _is_truthy(node.get("bacnetVisible")) and _normal_key(node.get("bacnetObjectType")) != "OBJECT_DISABLED":
            _add_conflict_key(
                conflict_groups,
                "bacnet_object_conflict",
                (_normal_key(node.get("bacnetObjectType")), _normal_key(node.get("bacnetObjectInstance"))),
                node_id,
            )

        if node_type == "bacipOutput":
            _add_conflict_key(
                conflict_groups,
                "bacip_point_conflict",
                (
                    _normal_key(node.get("bacnetIpDeviceInstance")),
                    _normal_key(node.get("bacnetIpObjectType")),
                    _normal_key(node.get("bacnetIpObjectInstance")),
                ),
                node_id,
            )

        if node_type in {"mqttin", "mqttout"}:
            _add_conflict_key(
                conflict_groups,
                "mqtt_object_name_conflict",
                (_normal_key(node.get("objectName")),),
                node_id,
            )

    messages = {
        "hardware_io_channel_conflict": "硬件 IO 通道重复。",
        "modbus_point_conflict": "Modbus 点位地址重复。",
        "bacnet_object_conflict": "本地 BACnet 对象类型和实例号重复。",
        "bacip_point_conflict": "BACnet/IP 远端点位重复。",
        "mqtt_object_name_conflict": "MQTT objectName 重复。",
    }
    for (code, key), node_ids in sorted(conflict_groups.items()):
        if len(node_ids) <= 1:
            continue
        _issue(
            issues,
            "error",
            code,
            f"{messages[code]} key={key}，冲突节点={node_ids}。",
            node_id=node_ids[0],
        )


def _add_conflict_key(
    groups: dict[tuple[str, tuple[str, ...]], list[str]],
    code: str,
    key: tuple[str, ...],
    node_id: str,
) -> None:
    if any(not item for item in key):
        return
    groups.setdefault((code, key), []).append(node_id)


def _normal_key(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    try:
        numeric = float(text)
    except ValueError:
        return text
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on", "是", "启用", "开启"}
    return False


def _build_report(issues: list[ValidationIssue]) -> dict[str, Any]:
    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    valid = error_count == 0
    blocked_export_reasons = [] if valid else ["存在 error 级校验问题。"]
    return {
        "valid": valid,
        "exportable": valid,
        "error_count": error_count,
        "warning_count": warning_count,
        "issue_count": len(issues),
        "issues": [asdict(issue) for issue in issues],
        "blocked_export_reasons": blocked_export_reasons,
    }
