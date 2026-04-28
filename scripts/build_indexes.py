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
BLOCK_INDEX_PATH = INDEXES_DIR / "blocks" / "block_index.jsonl"

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

FUNCTION_DEFINITIONS = {
    "pump_control": {
        "label": "水泵控制",
        "keywords": ["水泵", "冷冻泵", "冷却泵", "泵"],
        "risk_tags": ["equipment_count_sensitive"],
    },
    "bypass_valve_control": {
        "label": "旁通阀控制",
        "keywords": ["旁通", "旁通阀", "压差"],
        "risk_tags": ["control_loop"],
    },
    "valve_control": {
        "label": "阀门控制",
        "keywords": ["阀", "蝶阀", "水阀", "风阀"],
        "risk_tags": ["actuator_logic"],
    },
    "antifreeze_protection": {
        "label": "防冻保护",
        "keywords": ["防冻", "低温保护"],
        "risk_tags": ["protection_logic"],
    },
    "exhaust_fan_linkage": {
        "label": "排风机联动",
        "keywords": ["排风", "排风机"],
        "risk_tags": ["actuator_logic"],
    },
    "dx_unit_status": {
        "label": "直膨机状态",
        "keywords": ["直膨", "直膨机"],
        "risk_tags": ["equipment_status"],
    },
    "cooling_tower_control": {
        "label": "冷却塔控制",
        "keywords": ["冷却塔"],
        "risk_tags": ["equipment_count_sensitive"],
    },
    "chiller_control": {
        "label": "主机控制",
        "keywords": ["主机", "冷热泵", "热泵"],
        "risk_tags": ["equipment_count_sensitive"],
    },
    "fault_feedback": {
        "label": "故障反馈",
        "keywords": ["故障", "报警", "反馈"],
        "risk_tags": ["protection_logic"],
    },
    "pid_control": {
        "label": "PID 控制",
        "keywords": ["PID", "比例", "积分", "微分", "设定"],
        "risk_tags": ["control_loop"],
    },
    "co2_control": {
        "label": "CO2 控制",
        "keywords": ["CO2", "二氧化碳"],
        "risk_tags": ["air_quality_logic"],
    },
}

ANCHOR_TYPE_PRIORITY = {
    "comment": 0,
    "hwInput": 1,
    "hwOutput": 1,
    "modbusInput": 1,
    "modbusOutput": 1,
    "bacnetInput": 1,
    "bacnetOutput": 1,
    "swInput": 2,
    "compare": 3,
    "logic": 4,
    "pid": 4,
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


def extract_input_sources(wires: Any) -> list[str]:
    sources: list[str] = []
    if not isinstance(wires, list):
        return sources
    for input_sources in wires:
        if isinstance(input_sources, list):
            for source in input_sources:
                if isinstance(source, str):
                    sources.append(source)
                elif isinstance(source, dict) and isinstance(source.get("id"), str):
                    sources.append(source["id"])
        elif isinstance(input_sources, str):
            sources.append(input_sources)
        elif isinstance(input_sources, dict) and isinstance(input_sources.get("id"), str):
            sources.append(input_sources["id"])
    return sources


def collect_text(node: dict[str, Any], tab_label: str) -> str:
    values: list[str] = [tab_label]
    for key in ("type", "name", "label", "info", "topic", "objectName", "objctName"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return " ".join(values)


def collect_node_text(node: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("type", "name", "label", "info", "topic", "objectName", "objctName", "as"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    params = extract_key_params(node, max_items=6)
    for value in params.values():
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


def build_template_indexes(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
    nodes_by_tab: dict[str, list[dict[str, Any]]] = defaultdict(list)
    node_indexes: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for node in nodes:
        node_id = node.get("id")
        node_type = str(node.get("type", ""))
        tab_id = node.get("z")
        tab_label = tab_labels_by_id.get(tab_id, "")
        if tab_label:
            tab_node_counts[tab_id][node_type] += 1
        if isinstance(tab_id, str) and tab_label and node_type != "tab":
            nodes_by_tab[tab_id].append(node)

        node_text = collect_text(node, tab_label)
        if node_text:
            text_parts.append(node_text)

        if not isinstance(node_id, str) or node_type == "tab":
            continue

        input_sources = extract_input_sources(node.get("wires"))
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
                "input_sources": input_sources,
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
    block_indexes = build_block_indexes(
        template_id=template_id,
        project_type=project_type,
        source_path=relative_path,
        nodes_by_tab=nodes_by_tab,
        tab_labels_by_id=tab_labels_by_id,
    )
    return template_index, tab_indexes, node_indexes, block_indexes


def build_block_indexes(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    nodes_by_tab: dict[str, list[dict[str, Any]]],
    tab_labels_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    all_nodes = [node for tab_nodes in nodes_by_tab.values() for node in tab_nodes]
    node_by_id = {node.get("id"): node for node in all_nodes if isinstance(node.get("id"), str)}
    upstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    downstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for node_id, node in node_by_id.items():
        for source_id in extract_input_sources(node.get("wires")):
            if source_id in node_by_id:
                upstream_by_node[node_id].add(source_id)
                downstream_by_node[source_id].add(node_id)

    for tab_id, tab_nodes in sorted(nodes_by_tab.items()):
        tab_label = tab_labels_by_id.get(tab_id, tab_id)
        if not tab_nodes:
            continue
        blocks.append(_build_tab_overview_block(template_id, project_type, source_path, tab_id, tab_label, tab_nodes))
        for function_type, definition in FUNCTION_DEFINITIONS.items():
            block = _build_function_block(
                template_id=template_id,
                project_type=project_type,
                source_path=source_path,
                tab_id=tab_id,
                tab_label=tab_label,
                tab_nodes=tab_nodes,
                node_by_id=node_by_id,
                upstream_by_node=upstream_by_node,
                downstream_by_node=downstream_by_node,
                function_type=function_type,
                definition=definition,
            )
            if block is not None:
                blocks.append(block)
    return blocks


def _build_tab_overview_block(
    template_id: str,
    project_type: str,
    source_path: str,
    tab_id: str,
    tab_label: str,
    tab_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    type_counts = Counter(str(node.get("type", "")) for node in tab_nodes)
    node_ids = [str(node["id"]) for node in tab_nodes if isinstance(node.get("id"), str)]
    block_id = stable_id("block", f"{template_id}:{tab_id}:tab_overview")
    summary = f"{tab_label} 页面功能块，包含 {len(node_ids)} 个节点，主要类型为 {format_top_counts(type_counts)}。"
    return {
        "block_id": block_id,
        "template_id": template_id,
        "project_type": project_type,
        "source_path": source_path,
        "tab_id": tab_id,
        "tab_label": tab_label,
        "function_type": "tab_overview",
        "title": f"{tab_label} 总览",
        "summary": summary,
        "node_ids": node_ids[:160],
        "anchor_node_ids": node_ids[:8],
        "entry_node_ids": [],
        "exit_node_ids": [],
        "node_count": len(node_ids),
        "node_type_counts": dict(type_counts.most_common()),
        "risk_tags": _risk_tags_for_nodes(tab_nodes, []),
        "text_for_search": f"{tab_label} 总览 {summary}",
    }


def _build_function_block(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    tab_id: str,
    tab_label: str,
    tab_nodes: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    upstream_by_node: dict[str, set[str]],
    downstream_by_node: dict[str, set[str]],
    function_type: str,
    definition: dict[str, Any],
) -> dict[str, Any] | None:
    keywords = [str(keyword) for keyword in definition["keywords"]]
    tab_matches = any(keyword.casefold() in tab_label.casefold() for keyword in keywords)
    matched_nodes: list[dict[str, Any]] = []
    for node in tab_nodes:
        text = collect_node_text(node)
        if any(keyword.casefold() in text.casefold() for keyword in keywords):
            matched_nodes.append(node)
    if not matched_nodes and not tab_matches:
        return None
    anchors = _select_anchor_nodes(matched_nodes if matched_nodes else tab_nodes)
    selected_ids: set[str] = {str(node["id"]) for node in anchors if isinstance(node.get("id"), str)}
    for node_id in list(selected_ids):
        for neighbor_id in sorted(upstream_by_node.get(node_id, set()) | downstream_by_node.get(node_id, set())):
            neighbor = node_by_id.get(neighbor_id)
            if neighbor is not None and neighbor.get("z") == tab_id:
                selected_ids.add(neighbor_id)

    if len(selected_ids) < 3:
        selected_ids.update(_nearby_node_ids(tab_nodes, anchors, limit=12))
    if len(selected_ids) > 80:
        selected_ids = set(sorted(selected_ids)[:80])

    selected_nodes = [node_by_id[node_id] for node_id in sorted(selected_ids) if node_id in node_by_id]
    type_counts = Counter(str(node.get("type", "")) for node in selected_nodes)
    anchor_ids = [str(node["id"]) for node in anchors if isinstance(node.get("id"), str)]
    entry_ids = [
        node_id
        for node_id in sorted(selected_ids)
        if any(source_id not in selected_ids for source_id in upstream_by_node.get(node_id, set()))
    ][:12]
    exit_ids = [
        node_id
        for node_id in sorted(selected_ids)
        if any(target_id not in selected_ids for target_id in downstream_by_node.get(node_id, set()))
    ][:12]
    label = str(definition["label"])
    block_id = stable_id("block", f"{template_id}:{tab_id}:{function_type}")
    summary = f"{tab_label} 页面中的{label}功能块，包含 {len(selected_nodes)} 个局部节点，锚点 {len(anchor_ids)} 个。"
    risk_tags = _risk_tags_for_nodes(selected_nodes, list(definition.get("risk_tags", [])))
    return {
        "block_id": block_id,
        "template_id": template_id,
        "project_type": project_type,
        "source_path": source_path,
        "tab_id": tab_id,
        "tab_label": tab_label,
        "function_type": function_type,
        "title": f"{tab_label} - {label}",
        "summary": summary,
        "node_ids": sorted(selected_ids),
        "anchor_node_ids": anchor_ids[:12],
        "entry_node_ids": entry_ids,
        "exit_node_ids": exit_ids,
        "node_count": len(selected_nodes),
        "node_type_counts": dict(type_counts.most_common()),
        "risk_tags": risk_tags,
        "text_for_search": " ".join(
            [
                tab_label,
                label,
                " ".join(keywords),
                summary,
                " ".join(collect_node_text(node) for node in selected_nodes[:24]),
            ]
        ),
    }


def _select_anchor_nodes(nodes: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    def sort_key(node: dict[str, Any]) -> tuple[int, int, int, str]:
        node_type = str(node.get("type", ""))
        text_length = len(collect_node_text(node))
        x = int(node.get("x", 0)) if isinstance(node.get("x"), int) else 0
        return (ANCHOR_TYPE_PRIORITY.get(node_type, 9), -text_length, x, str(node.get("id", "")))

    anchors = [node for node in nodes if isinstance(node.get("id"), str)]
    anchors.sort(key=sort_key)
    return anchors[:limit]


def _nearby_node_ids(tab_nodes: list[dict[str, Any]], anchors: list[dict[str, Any]], *, limit: int) -> list[str]:
    anchor_positions = [
        (node.get("x"), node.get("y"))
        for node in anchors
        if isinstance(node.get("x"), int) and isinstance(node.get("y"), int)
    ]
    if not anchor_positions:
        return [str(node["id"]) for node in tab_nodes[:limit] if isinstance(node.get("id"), str)]

    scored: list[tuple[int, str]] = []
    for node in tab_nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not isinstance(node.get("x"), int) or not isinstance(node.get("y"), int):
            continue
        distance = min(abs(int(node["x"]) - int(anchor_x)) + abs(int(node["y"]) - int(anchor_y)) for anchor_x, anchor_y in anchor_positions)
        scored.append((distance, node_id))
    scored.sort()
    return [node_id for _, node_id in scored[:limit]]


def _risk_tags_for_nodes(nodes: list[dict[str, Any]], base_tags: list[str]) -> list[str]:
    tags = set(base_tags)
    node_types = {str(node.get("type", "")) for node in nodes}
    if any("modbus" in node_type.lower() or "bac" in node_type.lower() or "mqtt" in node_type.lower() for node_type in node_types):
        tags.add("io_communication")
    if {"hwInput", "hwOutput"} & node_types:
        tags.add("hard_io")
    text = " ".join(collect_node_text(node) for node in nodes)
    if any(keyword in text for keyword in ["防冻", "故障", "联锁", "保护"]):
        tags.add("protection_logic")
    if len(nodes) >= 60:
        tags.add("large_block")
    return sorted(tags)


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
    blocks: list[dict[str, Any]] = []

    for path in template_files:
        template_index, tab_indexes, node_indexes, block_indexes = build_template_indexes(path)
        templates.append(template_index)
        tabs.extend(tab_indexes)
        nodes.extend(node_indexes)
        blocks.extend(block_indexes)

    write_json(TEMPLATE_INDEX_PATH, templates)
    write_jsonl(TAB_INDEX_PATH, tabs)
    write_jsonl(NODE_INDEX_PATH, nodes)
    write_jsonl(BLOCK_INDEX_PATH, blocks)

    print(f"已生成模板索引: {relative_posix(TEMPLATE_INDEX_PATH)}")
    print(f"已生成页面索引: {relative_posix(TAB_INDEX_PATH)}")
    print(f"已生成节点索引: {relative_posix(NODE_INDEX_PATH)}")
    print(f"已生成功能块索引: {relative_posix(BLOCK_INDEX_PATH)}")
    print(f"模板数量: {len(templates)}")
    print(f"页面数量: {len(tabs)}")
    print(f"节点数量: {len(nodes)}")
    print(f"功能块数量: {len(blocks)}")
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
