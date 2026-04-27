from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
PROGRAMS_DIR = ROOT_DIR / "programs"
INDEXES_DIR = ROOT_DIR / "indexes"

TEMPLATE_INDEX_PATH = INDEXES_DIR / "templates" / "template_index.json"
TAB_INDEX_PATH = INDEXES_DIR / "tabs" / "tab_index.jsonl"
NODE_INDEX_PATH = INDEXES_DIR / "nodes" / "node_index.jsonl"

PROJECT_TYPE_DIRS = {
    "机房群控程序": "plant_room",
    "AHU程序": "ahu",
}

PROJECT_TYPE_LABELS = {
    "plant_room": "机房群控程序",
    "ahu": "AHU程序",
}

KEY_PARAM_EXCLUDES = {
    "id",
    "type",
    "z",
    "name",
    "x",
    "y",
    "wires",
    "inputs",
    "outputs",
}


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"模板根节点必须是数组: {path}")
    return data


def stable_id(prefix: str, relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def relative_posix(path: Path) -> str:
    return path.relative_to(ROOT_DIR).as_posix()


def detect_project_type(path: Path) -> str:
    parts = set(path.parts)
    for dirname, project_type in PROJECT_TYPE_DIRS.items():
        if dirname in parts:
            return project_type
    return "unknown"


def compact_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"object[{len(value)}]"
    return str(value)


def extract_key_params(node: dict[str, Any], max_items: int = 12) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in sorted(node):
        if key in KEY_PARAM_EXCLUDES:
            continue
        value = node[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            params[key] = value
        if len(params) >= max_items:
            break
    return params


def flatten_wires(wires: Any) -> list[str]:
    targets: list[str] = []
    if not isinstance(wires, list):
        return targets
    for output_targets in wires:
        if isinstance(output_targets, list):
            for target in output_targets:
                if isinstance(target, str):
                    targets.append(target)
                elif isinstance(target, dict) and isinstance(target.get("id"), str):
                    targets.append(target["id"])
        elif isinstance(output_targets, str):
            targets.append(output_targets)
        elif isinstance(output_targets, dict) and isinstance(output_targets.get("id"), str):
            targets.append(output_targets["id"])
    return targets


def collect_text(node: dict[str, Any], tab_label: str) -> str:
    values: list[str] = [tab_label]
    for key in ("type", "name", "label", "info", "topic", "objectName", "objctName"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return " ".join(values)


def infer_features(
    project_type: str,
    file_name: str,
    tab_labels: list[str],
    node_type_counts: Counter[str],
    all_text: str,
) -> dict[str, Any]:
    text = f"{file_name} {' '.join(tab_labels)} {all_text}"
    return {
        "has_pump_control": "水泵" in text or "泵" in text,
        "has_valve_control": "阀" in text,
        "has_bypass_valve": "旁通" in text,
        "has_cooling_tower": "冷却塔" in text,
        "has_air_source_heat_pump": "风冷热泵" in text or "风冷" in text,
        "has_ahu": project_type == "ahu" or "AHU" in text or "空调箱" in text,
        "has_exhaust_fan": "排风" in text,
        "has_dx_unit": "直膨" in text,
        "has_modbus": any(key.startswith("modbus") for key in node_type_counts),
        "has_bacnet": any("bac" in key.lower() for key in node_type_counts),
        "has_mqtt": any("mqtt" in key.lower() for key in node_type_counts),
        "has_hard_io": bool({"hwInput", "hwOutput"} & set(node_type_counts)),
    }


def build_summary(project_type: str, file_name: str, tab_labels: list[str], features: dict[str, Any]) -> str:
    label = PROJECT_TYPE_LABELS.get(project_type, project_type)
    matched_features: list[str] = []
    if features.get("has_air_source_heat_pump"):
        matched_features.append("风冷热泵")
    if features.get("has_pump_control"):
        matched_features.append("水泵")
    if features.get("has_valve_control"):
        matched_features.append("阀门")
    if features.get("has_bypass_valve"):
        matched_features.append("旁通阀")
    if features.get("has_cooling_tower"):
        matched_features.append("冷却塔")
    if features.get("has_dx_unit"):
        matched_features.append("直膨机")
    if features.get("has_exhaust_fan"):
        matched_features.append("排风")
    if features.get("has_modbus"):
        matched_features.append("Modbus")
    feature_text = "、".join(matched_features) if matched_features else "常规控制逻辑"
    tab_text = "、".join(tab_labels[:8])
    return f"{file_name} 是 {label} 模板，包含 {feature_text}；主要页面包括：{tab_text}。"


def build_template_indexes(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = load_json(path)
    relative_path = relative_posix(path)
    project_type = detect_project_type(path)
    template_id = stable_id(project_type, relative_path)

    tab_nodes = [node for node in nodes if node.get("type") == "tab"]
    tab_by_id = {node.get("id"): node for node in tab_nodes if isinstance(node.get("id"), str)}
    tab_labels_by_id = {
        tab_id: str(node.get("label") or node.get("name") or tab_id)
        for tab_id, node in tab_by_id.items()
    }

    node_type_counts = Counter(str(node.get("type", "")) for node in nodes)
    tab_node_counts: dict[str, Counter[str]] = defaultdict(Counter)
    node_indexes: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for node in nodes:
        node_id = node.get("id")
        node_type = str(node.get("type", ""))
        tab_id = node.get("z")
        tab_label = tab_labels_by_id.get(tab_id, "")
        if tab_label:
            tab_node_counts[tab_id][node_type] += 1

        node_text = collect_text(node, tab_label)
        if node_text:
            text_parts.append(node_text)

        if not isinstance(node_id, str) or node_type == "tab":
            continue

        wires_to = flatten_wires(node.get("wires"))
        node_indexes.append(
            {
                "template_id": template_id,
                "source_path": relative_path,
                "node_id": node_id,
                "tab_id": tab_id if isinstance(tab_id, str) else None,
                "tab_label": tab_label,
                "type": node_type,
                "name": node.get("name", ""),
                "inputs": node.get("inputs"),
                "outputs": node.get("outputs"),
                "wires_to": wires_to,
                "key_params": extract_key_params(node),
                "text_for_search": node_text,
            }
        )

    tab_indexes: list[dict[str, Any]] = []
    for tab in tab_nodes:
        tab_id = tab.get("id")
        if not isinstance(tab_id, str):
            continue
        type_counts = dict(tab_node_counts.get(tab_id, Counter()).most_common())
        tab_label = str(tab.get("label") or tab.get("name") or tab_id)
        tab_indexes.append(
            {
                "template_id": template_id,
                "source_path": relative_path,
                "tab_id": tab_id,
                "tab_label": tab_label,
                "node_count": sum(type_counts.values()),
                "node_type_counts": type_counts,
                "summary": f"{tab_label} 页面包含 {sum(type_counts.values())} 个节点，主要类型为 {format_top_counts(type_counts)}。",
            }
        )

    tab_labels = [item["tab_label"] for item in tab_indexes]
    all_text = " ".join(text_parts)
    features = infer_features(project_type, path.name, tab_labels, node_type_counts, all_text)
    template_index = {
        "template_id": template_id,
        "project_type": project_type,
        "project_type_label": PROJECT_TYPE_LABELS.get(project_type, project_type),
        "source_path": relative_path,
        "file_name": path.name,
        "node_count": len(nodes),
        "tab_count": len(tab_indexes),
        "tabs": tab_labels,
        "node_type_counts": dict(node_type_counts.most_common()),
        "features": features,
        "summary": build_summary(project_type, path.name, tab_labels, features),
    }
    return template_index, tab_indexes, node_indexes


def format_top_counts(counts: dict[str, int] | Counter[str], limit: int = 6) -> str:
    if isinstance(counts, Counter):
        items = counts.most_common(limit)
    else:
        items = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    return "、".join(f"{key}:{value}" for key, value in items if key) or "无"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")


def find_template_files() -> list[Path]:
    template_files: list[Path] = []
    for dirname in PROJECT_TYPE_DIRS:
        base_dir = PROGRAMS_DIR / dirname
        if base_dir.exists():
            template_files.extend(sorted(base_dir.rglob("*.json")))
    return sorted(template_files, key=lambda item: relative_posix(item))


def main() -> None:
    template_files = find_template_files()
    if not template_files:
        raise SystemExit("未找到模板 JSON 文件。")

    templates: list[dict[str, Any]] = []
    tabs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    for path in template_files:
        template_index, tab_indexes, node_indexes = build_template_indexes(path)
        templates.append(template_index)
        tabs.extend(tab_indexes)
        nodes.extend(node_indexes)

    write_json(TEMPLATE_INDEX_PATH, templates)
    write_jsonl(TAB_INDEX_PATH, tabs)
    write_jsonl(NODE_INDEX_PATH, nodes)

    print(f"已生成模板索引: {relative_posix(TEMPLATE_INDEX_PATH)}")
    print(f"已生成页面索引: {relative_posix(TAB_INDEX_PATH)}")
    print(f"已生成节点索引: {relative_posix(NODE_INDEX_PATH)}")
    print(f"模板数量: {len(templates)}")
    print(f"页面数量: {len(tabs)}")
    print(f"节点数量: {len(nodes)}")
    print("")
    print("模板摘要:")
    for template in templates:
        top_types = format_top_counts(template["node_type_counts"])
        print(
            f"- [{template['project_type_label']}] {template['file_name']} "
            f"节点:{template['node_count']} 页面:{template['tab_count']} 类型:{top_types}"
        )


if __name__ == "__main__":
    main()
