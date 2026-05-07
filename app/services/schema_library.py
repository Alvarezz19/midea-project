from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path
from typing import Any

from app.services.json_project import ROOT_DIR


SCHEMAS_DIR = ROOT_DIR / "schemas"


class SchemaLibraryError(ValueError):
    """模块 schema 读取、校验或节点生成失败。"""


def load_schema_files(schemas_dir: str | Path = SCHEMAS_DIR) -> list[dict[str, Any]]:
    base_dir = _resolve_schema_dir(schemas_dir)
    items: list[dict[str, Any]] = []
    for path in sorted(base_dir.rglob("*.json")):
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise SchemaLibraryError(f"schema 文件根节点必须是对象: {path}")
        relative_path = path.relative_to(ROOT_DIR).as_posix() if path.is_relative_to(ROOT_DIR) else str(path)
        items.append({"path": relative_path, "schema": data})
    return items


def get_schema(selector: dict[str, Any]) -> dict[str, Any]:
    """按 module_type、category、name 或 path 定位唯一 schema。"""

    if not isinstance(selector, dict) or not selector:
        raise SchemaLibraryError("schema_selector 必须是非空对象。")

    matches: list[dict[str, Any]] = []
    for item in load_schema_files():
        schema = item["schema"]
        path = item["path"]
        if "path" in selector and path != selector["path"]:
            continue
        if "module_type" in selector and schema.get("module_type") != selector["module_type"]:
            continue
        if "category" in selector and schema.get("category") != selector["category"]:
            continue
        if "name_contains" in selector and str(selector["name_contains"]) not in str(schema.get("name", "")):
            continue
        matches.append(item)

    if not matches:
        raise SchemaLibraryError(f"未找到匹配的 schema: {selector}")
    if len(matches) > 1:
        paths = [item["path"] for item in matches[:10]]
        raise SchemaLibraryError(f"schema_selector 匹配到 {len(matches)} 个 schema，必须唯一。样例: {paths}")

    result = copy.deepcopy(matches[0]["schema"])
    result["__schema_path__"] = matches[0]["path"]
    return result


def generate_nodes_from_schema(
    schema_selector: dict[str, Any],
    *,
    flow_id: str,
    params: dict[str, Any] | None = None,
    x: int = 120,
    y: int = 80,
    existing_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """根据 schema 的 template_json 生成合法节点。"""

    schema = get_schema(schema_selector)
    template = schema.get("template_json")
    if template is None:
        raise SchemaLibraryError(f"schema 缺少 template_json: {schema.get('__schema_path__')}")

    values = _build_param_values(schema, params or {})
    template_items = template if isinstance(template, list) else [template]
    if any(not isinstance(item, dict) for item in template_items):
        raise SchemaLibraryError("template_json 必须是对象或对象数组。")

    used_ids = set(existing_ids or set())
    nodes: list[dict[str, Any]] = []
    for index, template_item in enumerate(template_items):
        node_id = _new_node_id(used_ids)
        used_ids.add(node_id)
        context = {
            **values,
            "__GEN_ID__": node_id,
            "__FLOW_ID__": flow_id,
            "__POS_X__": x + index * 80,
            "__POS_Y__": y + index * 60,
            "wiresArray": [],
            "inputsOptionArray": values.get("inputsOption", []),
        }
        rendered = _render_template(copy.deepcopy(template_item), context)
        node = _normalize_generated_node(rendered)
        nodes.append(node)
    return nodes


def _build_param_values(schema: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise SchemaLibraryError("params 必须是对象。")

    schema_params = schema.get("parameters_schema") or {}
    if not isinstance(schema_params, dict):
        raise SchemaLibraryError("parameters_schema 必须是对象。")

    values: dict[str, Any] = {}
    for name, definition in schema_params.items():
        definition = definition if isinstance(definition, dict) else {}
        if name in params:
            value = params[name]
        elif "default" in definition:
            value = copy.deepcopy(definition["default"])
        elif name == "user_defined_name":
            value = schema.get("module_type") or schema.get("name") or "节点"
        else:
            raise SchemaLibraryError(f"缺少必需参数: {name}")
        values[name] = _coerce_and_validate_param(name, value, definition)

    unknown_params = sorted(set(params) - set(schema_params))
    if unknown_params:
        raise SchemaLibraryError(f"params 包含 schema 未定义参数: {unknown_params}")
    return values


def _coerce_and_validate_param(name: str, value: Any, definition: dict[str, Any]) -> Any:
    expected_type = str(definition.get("type", "")).lower()
    coerced = value

    if expected_type in {"integer", "int"} and not isinstance(value, bool):
        coerced = int(value)
    elif expected_type in {"number", "double", "float"} and not isinstance(value, bool):
        coerced = float(value)
        if coerced.is_integer():
            coerced = int(coerced)
    elif expected_type == "boolean":
        if isinstance(value, str):
            coerced = value.lower() in {"true", "1", "yes", "on", "是", "启用", "开启"}
        else:
            coerced = bool(value)
    elif expected_type == "string":
        coerced = str(value)
    elif "array" in expected_type:
        if isinstance(value, str):
            try:
                coerced = json.loads(value)
            except json.JSONDecodeError as exc:
                raise SchemaLibraryError(f"参数 {name} 必须是数组。") from exc
        if not isinstance(coerced, list):
            raise SchemaLibraryError(f"参数 {name} 必须是数组。")

    enum = definition.get("enum")
    if isinstance(enum, list) and enum and isinstance(coerced, list):
        invalid_items = [item for item in coerced if item not in enum]
        if invalid_items:
            raise SchemaLibraryError(f"参数 {name} 包含非法枚举项: {invalid_items}")
    elif isinstance(enum, list) and enum and coerced not in enum:
        raise SchemaLibraryError(f"参数 {name} 必须是枚举值之一: {enum}")

    minimum = definition.get("minimum")
    if isinstance(minimum, (int, float)) and isinstance(coerced, (int, float)) and coerced < minimum:
        raise SchemaLibraryError(f"参数 {name} 小于最小值 {minimum}。")

    maximum = definition.get("maximum")
    if isinstance(maximum, (int, float)) and isinstance(coerced, (int, float)) and coerced > maximum:
        raise SchemaLibraryError(f"参数 {name} 大于最大值 {maximum}。")

    return coerced


def _render_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    if not isinstance(value, str):
        return value

    exact_match = re.fullmatch(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", value)
    if exact_match:
        key = exact_match.group(1)
        if key not in context:
            raise SchemaLibraryError(f"template_json 使用了未知占位符: {key}")
        return copy.deepcopy(context[key])

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise SchemaLibraryError(f"template_json 使用了未知占位符: {key}")
        return str(context[key])

    return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", replace, value)


def _normalize_generated_node(node: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(node.get("id"), str) or not node["id"].strip():
        raise SchemaLibraryError("生成节点缺少有效 id。")
    if not isinstance(node.get("type"), str) or not node["type"].strip():
        raise SchemaLibraryError("生成节点缺少有效 type。")
    if not isinstance(node.get("z"), str) or not node["z"].strip():
        raise SchemaLibraryError("生成节点缺少有效 z。")

    node["inputs"] = _as_non_negative_int(node.get("inputs", 0), "inputs")
    node["outputs"] = _as_non_negative_int(node.get("outputs", 0), "outputs")
    node["x"] = _as_int(node.get("x", 120), "x")
    node["y"] = _as_int(node.get("y", 80), "y")

    wires = node.get("wires", [])
    if isinstance(wires, str):
        try:
            wires = json.loads(wires)
        except json.JSONDecodeError as exc:
            raise SchemaLibraryError("生成节点 wires 字符串不是合法 JSON。") from exc
    if not isinstance(wires, list):
        raise SchemaLibraryError("生成节点 wires 必须是数组。")

    normalized_wires: list[list[Any]] = []
    for index in range(node["inputs"]):
        item = wires[index] if index < len(wires) else []
        if not isinstance(item, list):
            raise SchemaLibraryError("生成节点 wires 的每个输入端口必须是数组。")
        normalized_wires.append(item)
    node["wires"] = normalized_wires
    return node


def _as_non_negative_int(value: Any, field: str) -> int:
    result = _as_int(value, field)
    if result < 0:
        raise SchemaLibraryError(f"{field} 必须是非负整数。")
    return result


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SchemaLibraryError(f"{field} 必须是整数。") from exc


def _new_node_id(existing_ids: set[str]) -> str:
    while True:
        node_id = uuid.uuid4().hex[:7]
        if node_id not in existing_ids:
            return node_id


def _resolve_schema_dir(path: str | Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    if not target.exists() or not target.is_dir():
        raise SchemaLibraryError(f"schema 目录不存在: {target}")
    return target
