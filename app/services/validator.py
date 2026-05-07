from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
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
PROTECTION_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "protection_logic.json"
DEFAULT_PROTECTION_RULES = {
    "text_fields": ("name", "label", "labelName", "labelNameOld", "info", "objectName", "topic"),
    "protection_signal_keywords": ("防冻", "故障", "反馈", "过滤", "滤网", "缺风", "报警", "到位", "保护", "联锁", "急停"),
    "critical_actuator": {
        "node_types": ("hwOutput", "modbusOutput", "bacipOutput", "mqttout"),
        "keywords": (
            "送风机",
            "排风机",
            "新风阀",
            "回风阀",
            "直膨",
            "水泵",
            "冷冻泵",
            "冷却泵",
            "主机",
            "热泵",
            "蝶阀",
            "旁通阀",
            "电加热",
        ),
    },
    "direct_bypass": {
        "source_types": ("constInput",),
        "source_keywords": ("常量", "强制", "旁路"),
        "message": "关键执行器输出不应只由常量、强制或旁路信号直接驱动。",
    },
    "upstream_chain_rules": (
        {
            "id": "ahu_freeze_to_air_actuators",
            "source_keywords": ("防冻",),
            "target_keywords": ("送风机", "新风阀"),
            "required_upstream_keywords": ("防冻",),
            "max_depth": 6,
            "message": "存在防冻保护信号时，送风机或新风阀执行器上游链路应保留防冻保护线索。",
        },
    ),
    "direction_rules": {
        "source_input_types": ("hwInput", "mqttin"),
        "physical_output_types": ("hwOutput",),
        "read_signal_keywords": ("故障", "反馈", "状态", "报警", "防冻", "急停", "到位"),
        "input_has_upstream_message": "物理输入或订阅输入不应被上游节点驱动。",
        "output_read_signal_message": "物理输出点位名称不应表达故障、反馈、状态、报警、防冻、急停或到位等读信号。",
    },
    "exceptions": (),
    "requirement_rules": (
        {
            "id": "ahu_supply_air_status",
            "equipment_terms": ("送风",),
            "required_terms": ("故障", "缺风", "报警", "运行"),
            "message": "AHU 送风相关逻辑缺少故障、缺风、报警或运行反馈线索。",
        },
        {
            "id": "ahu_dx_fault",
            "equipment_terms": ("直膨",),
            "required_terms": ("故障",),
            "message": "AHU 直膨机相关逻辑缺少故障状态线索。",
        },
        {
            "id": "ahu_electric_heater_fault",
            "equipment_terms": ("电加热",),
            "required_terms": ("故障",),
            "message": "AHU 电加热相关逻辑缺少故障状态线索。",
        },
        {
            "id": "ahu_filter_alarm",
            "equipment_terms": ("过滤", "滤网"),
            "required_terms": ("报警",),
            "message": "AHU 过滤网相关逻辑缺少报警线索。",
        },
        {
            "id": "plant_pump_fault",
            "equipment_terms": ("水泵", "冷冻泵", "冷却泵"),
            "required_terms": ("故障",),
            "message": "水泵相关逻辑缺少故障状态线索。",
        },
        {
            "id": "plant_pump_running_feedback",
            "equipment_terms": ("水泵", "冷冻泵", "冷却泵"),
            "required_terms": ("运行", "反馈"),
            "message": "水泵相关逻辑缺少运行或反馈线索。",
        },
        {
            "id": "plant_chiller_fault",
            "equipment_terms": ("主机", "热泵"),
            "required_terms": ("故障",),
            "message": "主机或热泵相关逻辑缺少故障状态线索。",
        },
        {
            "id": "plant_chiller_running",
            "equipment_terms": ("主机", "热泵"),
            "required_terms": ("运行",),
            "message": "主机或热泵相关逻辑缺少运行状态线索。",
        },
        {
            "id": "plant_valve_feedback",
            "equipment_terms": ("蝶阀",),
            "required_terms": ("到位", "反馈"),
            "message": "蝶阀相关逻辑缺少到位或反馈线索。",
        },
        {
            "id": "plant_bypass_valve_limit",
            "equipment_terms": ("旁通阀",),
            "required_terms": ("限幅", "上限", "下限"),
            "message": "旁通阀相关逻辑缺少上限、下限或限幅线索。",
        },
    ),
}


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    node_id: str | None = None
    path: str | None = None
    suggestion: str | None = None


def _issue(
    issues: list[ValidationIssue],
    severity: Severity,
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    path: str | None = None,
    suggestion: str | None = None,
) -> None:
    issues.append(
        ValidationIssue(
            severity=severity,
            code=code,
            message=message,
            node_id=node_id,
            path=path,
            suggestion=suggestion,
        )
    )


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
    _validate_protection_logic_rules(nodes, issues)

    return _build_report(issues)


def validate_project_change(before_nodes: Any, after_nodes: Any) -> dict[str, Any]:
    """校验工程变更风险，关注删除、断线和保护链路影响。"""

    issues: list[ValidationIssue] = []
    if not isinstance(before_nodes, list) or not isinstance(after_nodes, list):
        _issue(issues, "error", "change_nodes_not_array", "变更风险校验需要变更前后的工程节点数组。")
        return _build_report(issues)

    rules = _protection_rules()
    text_fields = rules["text_fields"]
    before_lookup = _node_lookup(before_nodes)
    after_lookup = _node_lookup(after_nodes)

    removed_ids = set(before_lookup) - set(after_lookup)
    for node_id in sorted(removed_ids):
        before_node = before_lookup[node_id]
        node_text = _node_text(before_node, text_fields)
        if _has_any(node_text, rules["protection_signal_keywords"]):
            _issue(
                issues,
                "error",
                "change_removed_protection_node",
                "变更删除了保护、故障、反馈或报警相关节点。",
                node_id=node_id,
                suggestion="恢复该节点，或提供明确授权并补充替代保护链路。",
            )
        if _is_critical_actuator(before_node, rules, text_fields):
            _issue(
                issues,
                "error",
                "change_removed_critical_actuator",
                "变更删除了关键执行器输出节点。",
                node_id=node_id,
                suggestion="确认设备数量变化并保留回滚点；若为误删，应恢复执行器节点。",
            )

    for node_id in sorted(set(before_lookup) & set(after_lookup)):
        before_node = before_lookup[node_id]
        after_node = after_lookup[node_id]
        before_text = _node_text(before_node, text_fields)
        after_text = _node_text(after_node, text_fields)

        if _has_any(before_text, rules["protection_signal_keywords"]) and not _has_any(after_text, rules["protection_signal_keywords"]):
            _issue(
                issues,
                "warning",
                "change_removed_protection_keyword",
                "变更移除了节点名称或说明中的保护线索关键词。",
                node_id=node_id,
                suggestion="确认只是命名调整；如影响保护语义，应恢复保护关键词或补充说明。",
            )

        removed_source_ids = _removed_wire_source_ids(before_node, after_node)
        for source_id in sorted(removed_source_ids):
            source_node = before_lookup.get(source_id)
            source_text = _node_text(source_node, text_fields) if source_node else ""
            if _has_any(source_text, rules["protection_signal_keywords"]) or _is_critical_actuator(before_node, rules, text_fields):
                _issue(
                    issues,
                    "error",
                    "change_removed_protection_wire",
                    "变更断开了保护相关信号或关键执行器的上游链路。",
                    node_id=node_id,
                    path="wires",
                    suggestion="恢复断开的连线，或在 dry-run 摘要中明确替代联锁路径并人工确认。",
                )

    return _build_report(issues)


def merge_validation_reports(*reports: dict[str, Any]) -> dict[str, Any]:
    """合并多个校验报告，保留统一 summary 和导出阻塞原因。"""

    issues: list[ValidationIssue] = []
    for report in reports:
        for item in report.get("issues", []):
            if not isinstance(item, dict):
                continue
            issues.append(
                ValidationIssue(
                    severity=item.get("severity") if item.get("severity") in {"error", "warning"} else "error",
                    code=str(item.get("code") or "unknown_validation_issue"),
                    message=str(item.get("message") or ""),
                    node_id=item.get("node_id") if isinstance(item.get("node_id"), str) else None,
                    path=item.get("path") if isinstance(item.get("path"), str) else None,
                    suggestion=item.get("suggestion") if isinstance(item.get("suggestion"), str) else None,
                )
            )
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


def _validate_protection_logic_rules(nodes: list[Any], issues: list[ValidationIssue]) -> None:
    rules = _protection_rules()
    text_fields = rules["text_fields"]
    node_lookup = _node_lookup(nodes)

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue

        path = f"$[{index}]"
        node_id = node.get("id") if isinstance(node.get("id"), str) else None
        node_text = _node_text(node, text_fields)

        if _has_any(node_text, rules["protection_signal_keywords"]) and _is_truthy(node.get("outOfService")):
            _issue(
                issues,
                "error",
                "protection_signal_disabled",
                "保护、故障、反馈或报警相关信号不应被设置为 outOfService。",
                node_id=node_id,
                path=f"{path}.outOfService",
            )

        _validate_direction_rules(node, rules, text_fields, issues, node_id=node_id, path=path)

        if _is_unwired_critical_actuator(node, rules, text_fields):
            _issue(
                issues,
                "error",
                "critical_actuator_without_interlock",
                "关键执行器输出缺少上游联锁或控制输入，可能绕过保护逻辑。",
                node_id=node_id,
                path=f"{path}.wires",
            )

        if _is_direct_bypass_critical_actuator(node, node_lookup, rules, text_fields) and not _is_protection_exception(
            rules["exceptions"],
            "critical_actuator_direct_bypass",
            node,
            node_text,
        ):
            _issue(
                issues,
                "error",
                "critical_actuator_direct_bypass",
                str(rules["direct_bypass"]["message"]),
                node_id=node_id,
                path=f"{path}.wires",
            )

        _validate_upstream_chain_rules(node, nodes, node_lookup, rules, text_fields, issues, node_id=node_id, path=path)

    for rule in rules["requirement_rules"]:
        equipment_terms = rule["equipment_terms"]
        required_terms = rule["required_terms"]
        if _has_equipment(nodes, equipment_terms, text_fields) and not _has_equipment_protection(
            nodes,
            equipment_terms,
            required_terms,
            text_fields,
        ):
            _issue(
                issues,
                "error",
                "missing_protection_logic",
                str(rule["message"]),
            )


@lru_cache(maxsize=1)
def _protection_rules() -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if PROTECTION_RULES_PATH.exists():
        with PROTECTION_RULES_PATH.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            raise ValueError("保护逻辑规则文件根节点必须是对象。")
        raw = loaded

    critical_raw = raw.get("critical_actuator")
    critical_default = DEFAULT_PROTECTION_RULES["critical_actuator"]
    critical = critical_raw if isinstance(critical_raw, dict) else {}
    direct_bypass_raw = raw.get("direct_bypass")
    direct_bypass_default = DEFAULT_PROTECTION_RULES["direct_bypass"]
    direct_bypass = direct_bypass_raw if isinstance(direct_bypass_raw, dict) else {}
    direction_raw = raw.get("direction_rules")
    direction_default = DEFAULT_PROTECTION_RULES["direction_rules"]
    direction = direction_raw if isinstance(direction_raw, dict) else {}

    return {
        "text_fields": _tuple_strings(raw.get("text_fields"), DEFAULT_PROTECTION_RULES["text_fields"]),
        "protection_signal_keywords": _tuple_strings(
            raw.get("protection_signal_keywords"),
            DEFAULT_PROTECTION_RULES["protection_signal_keywords"],
        ),
        "critical_actuator": {
            "node_types": set(_tuple_strings(critical.get("node_types"), critical_default["node_types"])),
            "keywords": _tuple_strings(critical.get("keywords"), critical_default["keywords"]),
        },
        "direct_bypass": {
            "source_types": set(_tuple_strings(direct_bypass.get("source_types"), direct_bypass_default["source_types"])),
            "source_keywords": _tuple_strings(
                direct_bypass.get("source_keywords"),
                direct_bypass_default["source_keywords"],
            ),
            "message": _string_value(direct_bypass.get("message"), direct_bypass_default["message"]),
        },
        "upstream_chain_rules": _load_upstream_chain_rules(
            raw.get("upstream_chain_rules"),
            DEFAULT_PROTECTION_RULES["upstream_chain_rules"],
        ),
        "direction_rules": {
            "source_input_types": set(
                _tuple_strings(direction.get("source_input_types"), direction_default["source_input_types"])
            ),
            "physical_output_types": set(
                _tuple_strings(direction.get("physical_output_types"), direction_default["physical_output_types"])
            ),
            "read_signal_keywords": _tuple_strings(
                direction.get("read_signal_keywords"),
                direction_default["read_signal_keywords"],
            ),
            "input_has_upstream_message": _string_value(
                direction.get("input_has_upstream_message"),
                direction_default["input_has_upstream_message"],
            ),
            "output_read_signal_message": _string_value(
                direction.get("output_read_signal_message"),
                direction_default["output_read_signal_message"],
            ),
        },
        "exceptions": _load_protection_exceptions(raw.get("exceptions"), DEFAULT_PROTECTION_RULES["exceptions"]),
        "requirement_rules": _load_requirement_rules(
            raw.get("requirement_rules"),
            DEFAULT_PROTECTION_RULES["requirement_rules"],
        ),
    }


def _tuple_strings(value: Any, default: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) and item.strip() for item in value):
        return tuple(value)
    return tuple(default)


def _string_value(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return default


def _int_value(value: Any, default: int, *, minimum: int = 1, maximum: int = 20) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and minimum <= value <= maximum:
        return value
    return default


def _load_requirement_rules(value: Any, default: Any) -> tuple[dict[str, Any], ...]:
    raw_rules = value if isinstance(value, list) else default
    rules: list[dict[str, Any]] = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue
        equipment_terms = _tuple_strings(raw_rule.get("equipment_terms"), ())
        required_terms = _tuple_strings(raw_rule.get("required_terms"), ())
        message = raw_rule.get("message")
        if not equipment_terms or not required_terms or not isinstance(message, str) or not message.strip():
            continue
        rules.append(
            {
                "id": str(raw_rule.get("id") or "unnamed"),
                "equipment_terms": equipment_terms,
                "required_terms": required_terms,
                "message": message,
            }
        )
    return tuple(rules)


def _load_upstream_chain_rules(value: Any, default: Any) -> tuple[dict[str, Any], ...]:
    raw_rules = value if isinstance(value, list) else default
    rules: list[dict[str, Any]] = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue
        rule_id = raw_rule.get("id")
        source_keywords = _tuple_strings(raw_rule.get("source_keywords"), ())
        target_keywords = _tuple_strings(raw_rule.get("target_keywords"), ())
        required_upstream_keywords = _tuple_strings(raw_rule.get("required_upstream_keywords"), ())
        message = raw_rule.get("message")
        if (
            not isinstance(rule_id, str)
            or not rule_id.strip()
            or not source_keywords
            or not target_keywords
            or not required_upstream_keywords
            or not isinstance(message, str)
            or not message.strip()
        ):
            continue
        rules.append(
            {
                "id": rule_id,
                "source_keywords": source_keywords,
                "target_keywords": target_keywords,
                "required_upstream_keywords": required_upstream_keywords,
                "max_depth": _int_value(raw_rule.get("max_depth"), 6),
                "message": message,
            }
        )
    return tuple(rules)


def _load_protection_exceptions(value: Any, default: Any) -> tuple[dict[str, Any], ...]:
    raw_items = value if isinstance(value, list) else default
    exceptions: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        rule_ids = _tuple_strings(raw_item.get("rule_ids"), ("*",))
        node_ids = _tuple_strings(raw_item.get("node_ids"), ())
        node_types = _tuple_strings(raw_item.get("node_types"), ())
        name_contains = _tuple_strings(raw_item.get("name_contains"), ())
        if not node_ids and not node_types and not name_contains:
            continue
        exceptions.append(
            {
                "rule_ids": rule_ids,
                "node_ids": set(node_ids),
                "node_types": set(node_types),
                "name_contains": name_contains,
            }
        )
    return tuple(exceptions)


def _is_unwired_critical_actuator(node: dict[str, Any], rules: dict[str, Any], text_fields: tuple[str, ...]) -> bool:
    critical = rules["critical_actuator"]
    if node.get("type") not in critical["node_types"]:
        return False
    if not _has_any(_node_text(node, text_fields), critical["keywords"]):
        return False
    inputs = node.get("inputs")
    if not isinstance(inputs, int) or inputs <= 0:
        return False
    wires = node.get("wires")
    if not isinstance(wires, list):
        return True
    return not any(isinstance(input_sources, list) and input_sources for input_sources in wires)


def _validate_direction_rules(
    node: dict[str, Any],
    rules: dict[str, Any],
    text_fields: tuple[str, ...],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    direction_rules = rules["direction_rules"]
    node_text = _node_text(node, text_fields)

    if (
        node.get("type") in direction_rules["source_input_types"]
        and _wire_source_ids(node)
        and not _is_protection_exception(rules["exceptions"], "input_signal_has_upstream_source", node, node_text)
    ):
        _issue(
            issues,
            "error",
            "input_signal_has_upstream_source",
            str(direction_rules["input_has_upstream_message"]),
            node_id=node_id,
            path=f"{path}.wires",
        )

    if (
        node.get("type") in direction_rules["physical_output_types"]
        and _has_any(node_text, direction_rules["read_signal_keywords"])
        and not _is_protection_exception(rules["exceptions"], "physical_output_looks_like_read_signal", node, node_text)
    ):
        _issue(
            issues,
            "error",
            "physical_output_looks_like_read_signal",
            str(direction_rules["output_read_signal_message"]),
            node_id=node_id,
            path=f"{path}.name",
        )


def _is_direct_bypass_critical_actuator(
    node: dict[str, Any],
    node_lookup: dict[str, dict[str, Any]],
    rules: dict[str, Any],
    text_fields: tuple[str, ...],
) -> bool:
    if not _is_critical_actuator(node, rules, text_fields):
        return False
    source_nodes = _direct_source_nodes(node, node_lookup)
    if not source_nodes:
        return False
    return all(_is_bypass_source(source_node, rules, text_fields) for source_node in source_nodes)


def _is_critical_actuator(node: dict[str, Any], rules: dict[str, Any], text_fields: tuple[str, ...]) -> bool:
    critical = rules["critical_actuator"]
    return node.get("type") in critical["node_types"] and _has_any(_node_text(node, text_fields), critical["keywords"])


def _is_bypass_source(node: dict[str, Any], rules: dict[str, Any], text_fields: tuple[str, ...]) -> bool:
    direct_bypass = rules["direct_bypass"]
    return node.get("type") in direct_bypass["source_types"] or _has_any(_node_text(node, text_fields), direct_bypass["source_keywords"])


def _validate_upstream_chain_rules(
    node: dict[str, Any],
    nodes: list[Any],
    node_lookup: dict[str, dict[str, Any]],
    rules: dict[str, Any],
    text_fields: tuple[str, ...],
    issues: list[ValidationIssue],
    *,
    node_id: str | None,
    path: str,
) -> None:
    if not _is_critical_actuator(node, rules, text_fields):
        return

    node_text = _node_text(node, text_fields)
    for rule in rules["upstream_chain_rules"]:
        rule_id = rule["id"]
        if not _has_any(node_text, rule["target_keywords"]):
            continue
        if not _has_equipment(nodes, rule["source_keywords"], text_fields):
            continue
        if _is_protection_exception(rules["exceptions"], rule_id, node, node_text):
            continue
        upstream_nodes = _upstream_nodes(node, node_lookup, max_depth=rule["max_depth"])
        upstream_text = " ".join(_node_text(upstream_node, text_fields) for upstream_node in upstream_nodes)
        if not _has_any(upstream_text, rule["required_upstream_keywords"]):
            _issue(
                issues,
                "error",
                "missing_protection_chain",
                str(rule["message"]),
                node_id=node_id,
                path=f"{path}.wires",
            )


def _node_lookup(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id.strip():
            result[node_id] = node
    return result


def _direct_source_nodes(node: dict[str, Any], node_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source_id in _wire_source_ids(node):
        source_node = node_lookup.get(source_id)
        if source_node is not None:
            result.append(source_node)
    return result


def _upstream_nodes(node: dict[str, Any], node_lookup: dict[str, dict[str, Any]], *, max_depth: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    frontier = [(source_id, 1) for source_id in _wire_source_ids(node)]
    while frontier:
        source_id, depth = frontier.pop(0)
        if source_id in seen or depth > max_depth:
            continue
        seen.add(source_id)
        source_node = node_lookup.get(source_id)
        if source_node is None:
            continue
        result.append(source_node)
        frontier.extend((next_source_id, depth + 1) for next_source_id in _wire_source_ids(source_node))
    return result


def _wire_source_ids(node: dict[str, Any]) -> list[str]:
    wires = node.get("wires")
    if not isinstance(wires, list):
        return []
    result: list[str] = []
    for input_sources in wires:
        if not isinstance(input_sources, list):
            continue
        for source in input_sources:
            source_id = source.get("id") if isinstance(source, dict) else source
            if isinstance(source_id, str) and source_id.strip():
                result.append(source_id)
    return result


def _removed_wire_source_ids(before_node: dict[str, Any], after_node: dict[str, Any]) -> set[str]:
    return set(_wire_source_ids(before_node)) - set(_wire_source_ids(after_node))


def _is_protection_exception(
    exceptions: tuple[dict[str, Any], ...],
    rule_id: str,
    node: dict[str, Any],
    node_text: str,
) -> bool:
    node_id = node.get("id")
    node_type = node.get("type")
    for exception in exceptions:
        rule_ids = exception["rule_ids"]
        if "*" not in rule_ids and rule_id not in rule_ids:
            continue
        node_ids = exception["node_ids"]
        if node_ids and node_id not in node_ids:
            continue
        node_types = exception["node_types"]
        if node_types and node_type not in node_types:
            continue
        name_contains = exception["name_contains"]
        if name_contains and not _has_any(node_text, name_contains):
            continue
        return True
    return False


def _node_text(node: dict[str, Any], text_fields: tuple[str, ...]) -> str:
    parts = []
    for field in text_fields:
        value = node.get(field)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts)


def _has_equipment(nodes: list[Any], equipment_terms: tuple[str, ...], text_fields: tuple[str, ...]) -> bool:
    return any(isinstance(node, dict) and _has_any(_node_text(node, text_fields), equipment_terms) for node in nodes)


def _has_equipment_protection(
    nodes: list[Any],
    equipment_terms: tuple[str, ...],
    required_terms: tuple[str, ...],
    text_fields: tuple[str, ...],
) -> bool:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = _node_text(node, text_fields)
        if _has_any(text, equipment_terms) and _has_any(text, required_terms):
            return True
    return False


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


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
    risk_count = sum(1 for issue in issues if issue.code.startswith("change_"))
    valid = error_count == 0
    blocked_export_reasons = [] if valid else ["存在 error 级校验问题。"]
    public_issues = []
    for issue in issues:
        item = asdict(issue)
        if item.get("suggestion") is None:
            item["suggestion"] = _suggestion_for_issue(issue.code)
        public_issues.append(item)
    return {
        "valid": valid,
        "exportable": valid,
        "summary": {
            "error_count": error_count,
            "warning_count": warning_count,
            "risk_count": risk_count,
        },
        "error_count": error_count,
        "warning_count": warning_count,
        "issue_count": len(issues),
        "issues": public_issues,
        "blocked_export_reasons": blocked_export_reasons,
    }


def _suggestion_for_issue(code: str) -> str:
    suggestions = {
        "root_not_array": "导出前确认工程 JSON 根节点是节点数组。",
        "node_not_object": "移除非对象数组项，或恢复为合法节点对象。",
        "missing_id": "为节点补充非空唯一 id。",
        "missing_type": "为节点补充合法 type，或从 schema 重新生成节点。",
        "duplicate_id": "重写重复节点 id，并同步修复引用该 id 的连线。",
        "unknown_flow_scope": "确认节点 z 字段引用的 tab 或 subflow 是否存在。",
        "invalid_wires": "将 wires 修正为按 input_sources 语义组织的数组。",
        "invalid_wire_input": "将该输入端口的 wires 项修正为上游源 id 数组。",
        "invalid_wire_source": "使用非空上游源 id，或包含 id 字段的源引用对象。",
        "missing_wire_source": "恢复缺失的上游源节点，或删除该无效连线。",
        "wires_inputs_mismatch": "同步调整 inputs 和 wires 长度，确保每个输入端口都有对应数组。",
        "schema_unknown_field": "移除未知字段，或先在 schema 中声明该参数。",
        "schema_missing_required_param": "按 schema 补齐必填参数。",
        "schema_invalid_type": "按 schema 要求修正字段类型。",
        "schema_invalid_enum": "使用 schema 允许的枚举值。",
        "schema_below_minimum": "将参数调高到 schema 最小值以上。",
        "schema_above_maximum": "将参数调低到 schema 最大值以下。",
        "hardware_io_channel_conflict": "调整硬件扩展板或通道，确保同一通道只绑定一个点位。",
        "modbus_point_conflict": "调整 Modbus 设备地址、功能码或寄存器地址，避免重复写同一点位。",
        "bacnet_object_conflict": "调整 BACnet 对象类型或实例号，避免本地对象重复。",
        "bacip_point_conflict": "调整 BACnet/IP 设备实例、对象类型或对象实例。",
        "mqtt_object_name_conflict": "调整 MQTT objectName，确保对象名唯一。",
        "protection_signal_disabled": "重新启用保护、故障、反馈或报警相关信号。",
        "critical_actuator_without_interlock": "为关键执行器恢复上游联锁或控制输入。",
        "critical_actuator_direct_bypass": "移除常量直连旁路，恢复经过联锁和保护的控制链路。",
        "missing_protection_chain": "恢复保护信号到目标执行器的上游链路，或补充等效保护路径。",
        "missing_protection_logic": "补充对应设备的故障、运行、反馈或到位保护线索。",
        "input_signal_has_upstream_source": "物理输入或订阅输入应作为信号源，移除其上游驱动连线。",
        "physical_output_looks_like_read_signal": "将读信号改为输入/状态发布点，或更正物理输出点位名称。",
    }
    return suggestions.get(code, "根据问题信息修复工程 JSON 后重新校验。")
