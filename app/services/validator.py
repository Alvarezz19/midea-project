from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Literal


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
