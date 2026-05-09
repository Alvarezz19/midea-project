from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.build_indexes import (
    detect_project_type,
    find_template_files,
    load_json,
    relative_posix,
    stable_id,
    write_json,
    write_jsonl,
)


ADVISORY_INDEX_DIR = ROOT_DIR / "indexes" / "advisory"

GENERIC_NODE_NAMES = {"变量", "引用", "PID控制器", "限值", "比较", "逻辑", "位组合", "取位", "位运算"}
ROLE_PRIORITY = [
    "protection",
    "alarm",
    "setpoint",
    "parameter",
    "feedback",
    "command",
    "sensor",
    "software_point",
    "point",
]

POINT_NODE_TYPES = {
    "swInput",
    "hwInput",
    "hwOutput",
    "modbusInput",
    "modbusOutput",
    "bacnetInput",
    "bacnetOutput",
    "bacipInput",
    "bacipOutput",
    "mqttin",
    "mqttout",
}

ALGORITHM_ROLE_BY_TYPE = {
    "enthalpy": "enthalpy_calculation",
    "wetBulbT": "wet_bulb_temperature",
    "dewpointT": "dew_point_temperature",
    "moisture": "moisture_content",
    "pid": "pid_control",
    "limit": "output_limit",
    "runtime": "runtime_accumulation",
    "sort": "rotation_sort",
    "statistics": "statistics",
}

TAB_ROLE_RULES = [
    ("configuration", ["使用配置", "配置"]),
    ("io_communication", ["IO/通讯", "硬接", "调用信号", "通讯信号"]),
    ("schedule_enable", ["定时"]),
    ("dx_status_mapping", ["直膨机状态"]),
    ("fault_mapping", ["故障"]),
    ("exhaust_fan_control", ["排风机", "排风"]),
    ("spare_points", ["备用点"]),
    ("equipment_standard_control", ["主机及蝶阀", "主机及其蝶阀", "冷却塔及蝶阀", "冷却塔及其蝶阀", "冷冻&冷却泵", "水泵控制", "蝶阀控制"]),
    ("system_sequence", ["动作先后判定", "联动控制"]),
    ("staging_rotation", ["加减载", "轮询"]),
    ("variable_frequency", ["变频"]),
    ("bypass_valve_control", ["旁通阀"]),
    ("dx_dehumidification", ["直膨除湿"]),
    ("main_control", ["风冷热泵控制", "控制"]),
]

CONFIG_ROLE_RULES = [
    ("device_count", ["总数量", "数量", "台数"]),
    ("topology", ["连接形式", "泵连形式", "连形式"]),
    ("mode", ["季节", "模式", "制冷", "制热"]),
    ("master_slave", ["单机", "从主", "主从"]),
    ("enable", ["启用", "使能"]),
]

SCOPE_RULES = [
    ("heat_pump", ["热泵", "机组", "主机", "冷机"]),
    ("pump", ["水泵", "冷冻泵", "冷却泵", "泵"]),
    ("cooling_tower", ["冷却塔", "塔"]),
    ("valve", ["阀", "蝶阀", "旁通阀"]),
    ("ahu", ["AHU", "空调箱", "送风机", "新风阀"]),
]


def build_advisory_kb(
    *,
    template_files: list[Path] | None = None,
) -> dict[str, Any]:
    if template_files is None:
        template_files = find_template_files()

    indexes: dict[str, list[dict[str, Any]]] = {
        "template_profile_index": [],
        "tab_role_index": [],
        "point_identity_index": [],
        "quote_reference_index": [],
        "point_usage_index": [],
        "subflow_index": [],
        "configuration_model_index": [],
        "bitmask_mapping_index": [],
        "algorithm_role_index": [],
        "control_chain_index": [],
        "protection_chain_index": [],
        "parameter_stat_index": [],
        "code_facts": [],
    }

    parameter_samples_by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    source_hashes: dict[str, str] = {}

    for path in sorted(template_files, key=lambda item: relative_posix(item)):
        nodes = load_json(path)
        source_path = relative_posix(path)
        source_hashes[source_path] = _sha256_file(path)
        template_records = build_template_advisory_records(path, nodes)
        for key, rows in template_records.items():
            if key == "parameter_samples":
                for sample in rows:
                    group_key = (
                        str(sample["project_type"]),
                        str(sample["function_type"]),
                        str(sample["parameter_name"]),
                        str(sample["unit"] or ""),
                    )
                    parameter_samples_by_key[group_key].append(sample)
            else:
                indexes[key].extend(rows)

    indexes["parameter_stat_index"] = _build_parameter_stats(parameter_samples_by_key)
    indexes["code_facts"] = _build_code_facts(indexes)

    manifest = {
        "schema_version": "advisory-kb-v1",
        "index_version": "phase-b-code-facts-v1",
        "source_count": len(template_files),
        "source_hashes": source_hashes,
        "row_counts": {key: len(value) for key, value in indexes.items()},
        "outputs": sorted(f"{key}.jsonl" for key in indexes),
    }

    return {"manifest": manifest, "indexes": indexes}


def build_template_advisory_records(path: Path, nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    source_path = relative_posix(path)
    project_type = detect_project_type(path)
    template_id = stable_id(project_type, source_path)
    node_by_id = {node["id"]: node for node in nodes if isinstance(node.get("id"), str)}
    tab_by_id = {
        node["id"]: node
        for node in nodes
        if node.get("type") == "tab" and isinstance(node.get("id"), str)
    }
    tab_labels = {tab_id: _node_display_name(tab) or tab_id for tab_id, tab in tab_by_id.items()}
    tab_roles = {tab_id: infer_tab_role(label) for tab_id, label in tab_labels.items()}
    upstream_by_node, downstream_by_node, input_refs_by_node = _build_graph_indexes(node_by_id)

    records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    configuration_records = _build_configuration_models(
        template_id=template_id,
        project_type=project_type,
        source_path=source_path,
        node_by_id=node_by_id,
        tab_labels=tab_labels,
        tab_roles=tab_roles,
    )
    records["configuration_model_index"].extend(configuration_records)

    for tab_id, tab in sorted(tab_by_id.items()):
        tab_label = tab_labels[tab_id]
        tab_nodes = [node for node in nodes if node.get("z") == tab_id and node.get("type") != "tab"]
        type_counts = Counter(str(node.get("type", "")) for node in tab_nodes)
        records["tab_role_index"].append(
            {
                "record_id": stable_id("tabrole", f"{template_id}:{tab_id}"),
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "tab_id": tab_id,
                "tab_label": tab_label,
                "tab_role": tab_roles[tab_id],
                "node_count": len(tab_nodes),
                "node_type_counts": dict(type_counts.most_common()),
            }
        )

    point_records, point_usage_groups = _build_point_records(
        template_id=template_id,
        project_type=project_type,
        source_path=source_path,
        node_by_id=node_by_id,
        tab_labels=tab_labels,
        tab_roles=tab_roles,
        downstream_by_node=downstream_by_node,
    )
    records["point_identity_index"].extend(point_records)

    quote_records = _build_quote_records(
        template_id=template_id,
        project_type=project_type,
        source_path=source_path,
        node_by_id=node_by_id,
        tab_labels=tab_labels,
        tab_roles=tab_roles,
        downstream_by_node=downstream_by_node,
    )
    records["quote_reference_index"].extend(quote_records)
    _merge_quote_usage(point_usage_groups, quote_records)
    records["point_usage_index"].extend(_build_point_usage_records(point_usage_groups))

    records["subflow_index"].extend(
        _build_subflow_records(
            template_id=template_id,
            project_type=project_type,
            source_path=source_path,
            node_by_id=node_by_id,
            tab_labels=tab_labels,
            tab_roles=tab_roles,
            input_refs_by_node=input_refs_by_node,
            downstream_by_node=downstream_by_node,
        )
    )

    records["bitmask_mapping_index"].extend(
        _build_bitmask_records(
            template_id=template_id,
            project_type=project_type,
            source_path=source_path,
            node_by_id=node_by_id,
            tab_labels=tab_labels,
            tab_roles=tab_roles,
            input_refs_by_node=input_refs_by_node,
            configuration_records=configuration_records,
        )
    )

    records["algorithm_role_index"].extend(
        _build_algorithm_records(
            template_id=template_id,
            project_type=project_type,
            source_path=source_path,
            node_by_id=node_by_id,
            tab_labels=tab_labels,
            tab_roles=tab_roles,
            upstream_by_node=upstream_by_node,
            downstream_by_node=downstream_by_node,
        )
    )

    records["control_chain_index"].extend(
        _build_control_chain_records(
            template_id=template_id,
            project_type=project_type,
            source_path=source_path,
            node_by_id=node_by_id,
            tab_labels=tab_labels,
            tab_roles=tab_roles,
            upstream_by_node=upstream_by_node,
            downstream_by_node=downstream_by_node,
        )
    )

    records["protection_chain_index"].extend(
        _build_protection_chain_records(
            template_id=template_id,
            project_type=project_type,
            source_path=source_path,
            node_by_id=node_by_id,
            tab_labels=tab_labels,
            tab_roles=tab_roles,
            downstream_by_node=downstream_by_node,
        )
    )

    records["template_profile_index"].append(
        _build_template_profile(
            path=path,
            template_id=template_id,
            project_type=project_type,
            source_path=source_path,
            nodes=nodes,
            tab_labels=tab_labels,
            tab_roles=tab_roles,
            configuration_records=configuration_records,
        )
    )

    records["parameter_samples"].extend(
        _build_parameter_samples(
            template_id=template_id,
            project_type=project_type,
            source_path=source_path,
            node_by_id=node_by_id,
            tab_labels=tab_labels,
            tab_roles=tab_roles,
            point_records=point_records,
        )
    )

    return dict(records)


def write_advisory_kb(
    data: dict[str, Any],
    *,
    output_dir: Path = ADVISORY_INDEX_DIR,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "advisory_kb_manifest.json", data["manifest"])
    for name, rows in sorted(data["indexes"].items()):
        write_jsonl(output_dir / f"{name}.jsonl", rows)


def infer_tab_role(tab_label: str) -> str:
    for role, keywords in TAB_ROLE_RULES:
        if any(keyword in tab_label for keyword in keywords):
            return role
    return "other"


def infer_point_roles(text: str, node_type: str) -> list[str]:
    roles: set[str] = set()
    lower_type = node_type.lower()
    if lower_type in {"hwinput", "modbusinput", "bacnetinput", "bacipinput", "mqttin"}:
        roles.add("sensor")
    if lower_type in {"hwoutput", "modbusoutput", "bacnetoutput", "bacipoutput", "mqttout"}:
        roles.add("command")
    if any(keyword in text for keyword in ["设定", "SP", "目标值", "上限", "下限"]):
        roles.add("setpoint")
    if any(keyword in text for keyword in ["比例", "积分", "微分", "死区", "延时", "回差", "限幅", "时间"]):
        roles.add("parameter")
    if any(keyword in text for keyword in ["防冻", "急停", "保护", "水流"]):
        roles.add("protection")
    if any(keyword in text for keyword in ["故障", "报警"]):
        roles.add("alarm")
    if any(keyword in text for keyword in ["反馈", "运行", "状态", "到位", "标志"]):
        roles.add("feedback")
    if any(keyword in text for keyword in ["命令", "控制", "输出", "启停", "开度", "频率"]):
        roles.add("command")
    if any(keyword in text for keyword in ["温度", "湿度", "CO2", "二氧化碳", "压差", "压力", "流量", "液位"]):
        roles.add("sensor")
    if not roles:
        roles.add("software_point" if node_type == "swInput" else "point")
    return [role for role in ROLE_PRIORITY if role in roles]


def infer_function_type(text: str, project_type: str) -> str:
    if "CO2" in text or "二氧化碳" in text:
        return "co2_control"
    if "防冻" in text or "低温保护" in text:
        return "freeze_protection"
    if "过滤网" in text or "滤网" in text:
        return "filter_alarm"
    if "直膨" in text:
        return "dx_unit_control"
    if "旁通" in text:
        return "bypass_valve_control"
    if "压差" in text:
        return "differential_pressure_control"
    if "水泵" in text or "冷冻泵" in text or "冷却泵" in text:
        return "pump_control"
    if "冷却塔" in text:
        return "cooling_tower_control"
    if "热泵" in text or "主机" in text or "冷机" in text:
        return "chiller_sequence" if project_type == "plant_room" else "dx_unit_control"
    if "送风机" in text or "风机" in text:
        return "fan_control"
    if "新风" in text or "回风" in text:
        return "fresh_return_air_damper_control"
    if "温度" in text:
        return "supply_air_temperature_control" if project_type == "ahu" else "temperature_control"
    return "general"


def _build_point_records(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    downstream_by_node: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    usage_groups: dict[str, dict[str, Any]] = {}
    for node_id, node in sorted(node_by_id.items()):
        node_type = str(node.get("type", ""))
        if node_type not in POINT_NODE_TYPES:
            continue
        tab_id = node.get("z") if isinstance(node.get("z"), str) else None
        tab_label = tab_labels.get(tab_id or "", "")
        tab_role = tab_roles.get(tab_id or "", "none")
        display_name = _node_display_name(node)
        text = _node_search_text(node, tab_label)
        point_roles = infer_point_roles(text, node_type)
        function_type = infer_function_type(text, project_type)
        canonical_key = _canonical_semantic_key(
            project_type=project_type,
            tab_role=tab_role,
            point_name=display_name,
            node_type=node_type,
            role=point_roles[0],
        )
        record = {
            "point_id": stable_id("point", f"{template_id}:{node_id}"),
            "template_id": template_id,
            "project_type": project_type,
            "source_path": source_path,
            "node_id": node_id,
            "tab_id": tab_id,
            "tab_label": tab_label,
            "tab_role": tab_role,
            "node_type": node_type,
            "point_name": display_name,
            "canonical_semantic_key": canonical_key,
            "semantic_fingerprint": {
                "project_type": project_type,
                "tab_role": tab_role,
                "equipment": infer_equipment(text),
                "point_name": display_name,
                "node_type": node_type,
                "role": point_roles[0],
            },
            "point_roles": point_roles,
            "function_type": function_type,
            "communication": _communication_metadata(node),
            "downstream_node_ids": sorted(downstream_by_node.get(node_id, set()))[:20],
            "text_for_search": text,
        }
        records.append(record)
        usage_groups[canonical_key] = {
            "usage_id": stable_id("pointusage", f"{template_id}:{canonical_key}"),
            "template_id": template_id,
            "project_type": project_type,
            "source_path": source_path,
            "canonical_semantic_key": canonical_key,
            "point_name": display_name,
            "point_roles": point_roles,
            "definitions": [node_id],
            "quotes": [],
            "tabs": sorted({tab_label} if tab_label else set()),
            "tab_roles": sorted({tab_role}),
            "downstream_node_ids": sorted(downstream_by_node.get(node_id, set()))[:40],
        }
    return records, usage_groups


def _build_quote_records(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    downstream_by_node: dict[str, set[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for node_id, node in sorted(node_by_id.items()):
        if node.get("type") != "quote":
            continue
        tab_id = node.get("z") if isinstance(node.get("z"), str) else None
        tab_label = tab_labels.get(tab_id or "", "")
        tab_role = tab_roles.get(tab_id or "", "none")
        parsed = parse_quote_label(str(node.get("labelName") or ""))
        referenced_node = node_by_id.get(parsed.get("node_id") or "")
        referenced_display = _node_display_name(referenced_node) if referenced_node else parsed.get("display") or ""
        quote_display = parsed.get("display") or referenced_display or _node_display_name(node)
        referenced_tab_id = referenced_node.get("z") if isinstance(referenced_node, dict) and isinstance(referenced_node.get("z"), str) else None
        text = " ".join(value for value in [quote_display, referenced_display, tab_label] if value)
        canonical_key = _canonical_semantic_key(
            project_type=project_type,
            tab_role=tab_roles.get(referenced_tab_id or "", tab_role),
            point_name=referenced_display or quote_display,
            node_type=str(referenced_node.get("type") if referenced_node else "quote"),
            role=(infer_point_roles(text, str(referenced_node.get("type") if referenced_node else "quote"))[0]),
        )
        records.append(
            {
                "quote_id": stable_id("quote", f"{template_id}:{node_id}"),
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "node_id": node_id,
                "tab_id": tab_id,
                "tab_label": tab_label,
                "tab_role": tab_role,
                "label_name": node.get("labelName"),
                "referenced_node_id": parsed.get("node_id"),
                "referenced_port": parsed.get("port"),
                "referenced_display": referenced_display,
                "referenced_tab_id": referenced_tab_id,
                "referenced_tab_label": tab_labels.get(referenced_tab_id or "", ""),
                "resolved": referenced_node is not None,
                "canonical_semantic_key": canonical_key,
                "downstream_node_ids": sorted(downstream_by_node.get(node_id, set()))[:20],
                "text_for_search": text,
            }
        )
    return records


def parse_quote_label(label_name: str) -> dict[str, Any]:
    match = re.match(r"^\[(?P<node_id>[^:\]]+)(?::(?P<port>[0-9]+))?\]\s*(?P<display>.*)$", label_name.strip())
    if not match:
        return {"node_id": None, "port": None, "display": label_name.strip()}
    port = match.group("port")
    return {
        "node_id": match.group("node_id"),
        "port": int(port) if port is not None else 0,
        "display": match.group("display").strip(),
    }


def _merge_quote_usage(usage_groups: dict[str, dict[str, Any]], quote_records: list[dict[str, Any]]) -> None:
    for quote in quote_records:
        canonical_key = str(quote.get("canonical_semantic_key") or "")
        if not canonical_key:
            continue
        group = usage_groups.setdefault(
            canonical_key,
            {
                "usage_id": stable_id("pointusage", f"{quote['template_id']}:{canonical_key}"),
                "template_id": quote["template_id"],
                "project_type": quote["project_type"],
                "source_path": quote["source_path"],
                "canonical_semantic_key": canonical_key,
                "point_name": quote.get("referenced_display") or quote.get("label_name") or "",
                "point_roles": infer_point_roles(str(quote.get("referenced_display") or ""), "quote"),
                "definitions": [],
                "quotes": [],
                "tabs": [],
                "tab_roles": [],
                "downstream_node_ids": [],
            },
        )
        _append_unique(group["quotes"], quote["node_id"])
        _append_unique(group["tabs"], str(quote.get("tab_label") or ""))
        _append_unique(group["tab_roles"], str(quote.get("tab_role") or ""))
        for node_id in quote.get("downstream_node_ids") or []:
            _append_unique(group["downstream_node_ids"], str(node_id))


def _build_point_usage_records(usage_groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for group in usage_groups.values():
        records.append(
            {
                **group,
                "definitions": sorted(set(group.get("definitions") or [])),
                "quotes": sorted(set(group.get("quotes") or [])),
                "tabs": sorted(item for item in set(group.get("tabs") or []) if item),
                "tab_roles": sorted(item for item in set(group.get("tab_roles") or []) if item),
                "downstream_node_ids": sorted(set(group.get("downstream_node_ids") or []))[:80],
                "definition_count": len(set(group.get("definitions") or [])),
                "quote_count": len(set(group.get("quotes") or [])),
            }
        )
    records.sort(key=lambda item: (item["template_id"], item["canonical_semantic_key"]))
    return records


def _build_subflow_records(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    input_refs_by_node: dict[str, list[dict[str, Any]]],
    downstream_by_node: dict[str, set[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    definitions = {node_id: node for node_id, node in node_by_id.items() if node.get("type") == "subflow"}
    instances_by_subflow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node_id, node in node_by_id.items():
        node_type = str(node.get("type", ""))
        if node_type.startswith("subflow:"):
            instances_by_subflow[node_type.split(":", 1)[1]].append(node)

    for subflow_id, node in sorted(definitions.items()):
        instances = instances_by_subflow.get(subflow_id, [])
        text = _node_search_text(node, "")
        records.append(
            {
                "record_id": stable_id("subflowdef", f"{template_id}:{subflow_id}"),
                "record_type": "subflow_definition",
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "subflow_id": subflow_id,
                "name": _node_display_name(node),
                "function_type": infer_function_type(text, project_type),
                "status_in_template": "instantiated" if instances else "defined_only",
                "instance_count": len(instances),
                "input_ports": _subflow_ports(node.get("in"), direction="input"),
                "output_ports": _subflow_ports(node.get("out"), direction="output"),
                "instances": [
                    {
                        "node_id": instance["id"],
                        "tab_id": instance.get("z"),
                        "tab_label": tab_labels.get(str(instance.get("z")), ""),
                        "tab_role": tab_roles.get(str(instance.get("z")), "none"),
                    }
                    for instance in instances
                    if isinstance(instance.get("id"), str)
                ],
                "text_for_search": text,
            }
        )

    for node_id, node in sorted(node_by_id.items()):
        node_type = str(node.get("type", ""))
        if not node_type.startswith("subflow:"):
            continue
        subflow_id = node_type.split(":", 1)[1]
        definition = definitions.get(subflow_id)
        tab_id = node.get("z") if isinstance(node.get("z"), str) else None
        records.append(
            {
                "record_id": stable_id("subflowinst", f"{template_id}:{node_id}"),
                "record_type": "subflow_instance",
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "node_id": node_id,
                "subflow_id": subflow_id,
                "definition_name": _node_display_name(definition) if definition else "",
                "tab_id": tab_id,
                "tab_label": tab_labels.get(tab_id or "", ""),
                "tab_role": tab_roles.get(tab_id or "", "none"),
                "input_bindings": _instance_input_bindings(node_id, input_refs_by_node, node_by_id),
                "output_bindings": _instance_output_bindings(node_id, downstream_by_node, node_by_id),
            }
        )
    return records


def _build_configuration_models(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for tab_id, tab_role in sorted(tab_roles.items()):
        if tab_role != "configuration":
            continue
        config_points: list[dict[str, Any]] = []
        for node in sorted(node_by_id.values(), key=lambda item: str(item.get("id", ""))):
            if node.get("z") != tab_id:
                continue
            text = _node_search_text(node, tab_labels.get(tab_id, ""))
            role = _infer_config_role(text)
            if role is None:
                continue
            config_points.append(
                {
                    "node_id": node.get("id"),
                    "name": _node_display_name(node),
                    "node_type": node.get("type"),
                    "role": role,
                    "scope": infer_scope(text),
                    "value": _first_existing_value(node, ["swInputDefault", "constant", "outOfServiceValue", "value"]),
                }
            )
        if config_points:
            records.append(
                {
                    "configuration_model_id": stable_id("config", f"{template_id}:{tab_id}"),
                    "template_id": template_id,
                    "project_type": project_type,
                    "source_path": source_path,
                    "tab_id": tab_id,
                    "tab_label": tab_labels.get(tab_id, ""),
                    "system_type": "air_source_heat_pump" if any("热泵" in str(item.get("name", "")) for item in config_points) else project_type,
                    "config_points": config_points,
                    "affects": _configuration_affects(config_points),
                }
            )
    return records


def _build_bitmask_records(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    input_refs_by_node: dict[str, list[dict[str, Any]]],
    configuration_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    has_configuration = bool(configuration_records)
    for node_id, node in sorted(node_by_id.items()):
        if node.get("type") not in {"bitGroup", "bitFetch", "bitOperation"}:
            continue
        tab_id = node.get("z") if isinstance(node.get("z"), str) else None
        refs = input_refs_by_node.get(node_id, [])
        bit_mapping: list[dict[str, Any]] = []
        for ref in refs:
            source_node = node_by_id.get(ref["source_id"])
            source_text = _node_display_name(source_node) if source_node else ref["source_id"]
            bit_index = int(ref["target_input_port"])
            bit_mapping.append(
                {
                    "bit": _node_bit_value(node, bit_index),
                    "source_node_id": ref["source_id"],
                    "source_name": source_text,
                    "device_no": _infer_device_no(source_text),
                    "equipment_type": infer_equipment_type(source_text),
                    "role": infer_point_roles(source_text, str(source_node.get("type") if source_node else ""))[0],
                }
            )
        records.append(
            {
                "bitmask_id": stable_id("bitmask", f"{template_id}:{node_id}"),
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "node_id": node_id,
                "node_type": node.get("type"),
                "tab_id": tab_id,
                "tab_label": tab_labels.get(tab_id or "", ""),
                "tab_role": tab_roles.get(tab_id or "", "none"),
                "chain_type": _bitmask_chain_type(node, bit_mapping),
                "device_array": {
                    "equipment_type": _dominant_equipment_type(bit_mapping),
                    "max_count": max((item["device_no"] or 0 for item in bit_mapping), default=int(node.get("inputs") or 0)),
                    "active_count_source": "configuration" if has_configuration else "fixed_template",
                    "bit_mapping": bit_mapping,
                },
                "text_for_search": " ".join(item["source_name"] for item in bit_mapping),
            }
        )
    return records


def _build_algorithm_records(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    upstream_by_node: dict[str, set[str]],
    downstream_by_node: dict[str, set[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for node_id, node in sorted(node_by_id.items()):
        node_type = str(node.get("type", ""))
        if node_type not in ALGORITHM_ROLE_BY_TYPE:
            continue
        tab_id = node.get("z") if isinstance(node.get("z"), str) else None
        neighborhood_text = _neighborhood_text(node_id, node_by_id, upstream_by_node, downstream_by_node)
        records.append(
            {
                "algorithm_id": stable_id("algo", f"{template_id}:{node_id}"),
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "node_id": node_id,
                "node_type": node_type,
                "algorithm_role": ALGORITHM_ROLE_BY_TYPE[node_type],
                "function_type": infer_function_type(neighborhood_text, project_type),
                "tab_id": tab_id,
                "tab_label": tab_labels.get(tab_id or "", ""),
                "tab_role": tab_roles.get(tab_id or "", "none"),
                "upstream_node_ids": sorted(upstream_by_node.get(node_id, set()))[:20],
                "downstream_node_ids": sorted(downstream_by_node.get(node_id, set()))[:20],
                "text_for_search": neighborhood_text,
            }
        )
    return records


def _build_control_chain_records(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    upstream_by_node: dict[str, set[str]],
    downstream_by_node: dict[str, set[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    anchor_types = {"pid", "compare", "limit", "logic", "switch"}
    for node_id, node in sorted(node_by_id.items()):
        if node.get("type") not in anchor_types:
            continue
        neighborhood = _bounded_neighborhood(node_id, upstream_by_node, downstream_by_node, max_depth=2, limit=24)
        text = " ".join(_node_display_name(node_by_id.get(item)) for item in neighborhood)
        function_type = infer_function_type(text, project_type)
        if function_type == "general" and node.get("type") not in {"pid", "limit"}:
            continue
        tab_id = node.get("z") if isinstance(node.get("z"), str) else None
        records.append(
            {
                "chain_id": stable_id("chain", f"{template_id}:{node_id}:control"),
                "chain_type": _control_chain_type(str(node.get("type")), text),
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "tab_ids": sorted({str(node_by_id[item].get("z")) for item in neighborhood if item in node_by_id and node_by_id[item].get("z")}),
                "tab_roles": sorted({tab_roles.get(str(node_by_id[item].get("z")), "none") for item in neighborhood if item in node_by_id}),
                "anchor_node_id": node_id,
                "function_type": function_type,
                "source_nodes": _chain_node_refs(sorted(upstream_by_node.get(node_id, set())), node_by_id),
                "logic_nodes": _chain_node_refs([item for item in neighborhood if str(node_by_id.get(item, {}).get("type")) in anchor_types], node_by_id),
                "target_nodes": _chain_node_refs(sorted(downstream_by_node.get(node_id, set())), node_by_id),
                "risk_tags": sorted(_risk_tags_for_text(text)),
                "summary": f"{tab_labels.get(tab_id or '', '')} 页面 {function_type} 控制链，锚点 {node_id}。",
                "boundary": {
                    "entry_node_ids": sorted(upstream_by_node.get(node_id, set()))[:12],
                    "exit_node_ids": sorted(downstream_by_node.get(node_id, set()))[:12],
                },
                "text_for_search": text,
            }
        )
    return records


def _build_protection_chain_records(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    downstream_by_node: dict[str, set[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for node_id, node in sorted(node_by_id.items()):
        text = _node_search_text(node, tab_labels.get(str(node.get("z")), ""))
        roles = infer_point_roles(text, str(node.get("type", "")))
        if "protection" not in roles and "alarm" not in roles:
            continue
        downstream = _downstream_closure(node_id, downstream_by_node, max_depth=3, limit=30)
        if not downstream:
            continue
        tab_id = node.get("z") if isinstance(node.get("z"), str) else None
        downstream_text = " ".join(_node_display_name(node_by_id.get(item)) for item in downstream)
        records.append(
            {
                "chain_id": stable_id("pchain", f"{template_id}:{node_id}"),
                "chain_type": "protection_to_actuator" if "protection" in roles else "fault_to_alarm",
                "template_id": template_id,
                "project_type": project_type,
                "source_path": source_path,
                "tab_ids": sorted({str(node_by_id[item].get("z")) for item in downstream if item in node_by_id and node_by_id[item].get("z")} | ({tab_id} if tab_id else set())),
                "tab_roles": sorted({tab_roles.get(str(node_by_id[item].get("z")), "none") for item in downstream if item in node_by_id}),
                "source_nodes": _chain_node_refs([node_id], node_by_id),
                "target_nodes": _chain_node_refs(downstream[:12], node_by_id),
                "risk_tags": sorted(_risk_tags_for_text(text + " " + downstream_text) | {"protection_logic"}),
                "summary": f"{_node_display_name(node)} 的保护或报警链路，影响 {len(downstream)} 个下游节点。",
                "boundary": {"entry_node_ids": [node_id], "exit_node_ids": downstream[:12]},
                "text_for_search": f"{text} {downstream_text}",
            }
        )
    return records


def _build_parameter_samples(
    *,
    template_id: str,
    project_type: str,
    source_path: str,
    node_by_id: dict[str, dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    point_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    point_by_node_id = {record["node_id"]: record for record in point_records}
    for node_id, node in sorted(node_by_id.items()):
        point_record = point_by_node_id.get(node_id)
        text = _node_search_text(node, tab_labels.get(str(node.get("z")), ""))
        roles = set(point_record.get("point_roles", [])) if point_record else set(infer_point_roles(text, str(node.get("type", ""))))
        if not (roles & {"setpoint", "parameter"} or node.get("type") in {"pid", "limit"}):
            continue
        numeric_values = _numeric_parameter_values(node)
        for field, value in numeric_values.items():
            display_name = point_record.get("point_name") if point_record else _node_display_name(node)
            parameter_name = display_name if field in {"swInputDefault", "constant", "outOfServiceValue"} else f"{display_name}.{field}"
            samples.append(
                {
                    "template_id": template_id,
                    "project_type": project_type,
                    "source_path": source_path,
                    "node_id": node_id,
                    "tab_id": node.get("z") if isinstance(node.get("z"), str) else None,
                    "tab_label": tab_labels.get(str(node.get("z")), ""),
                    "tab_role": tab_roles.get(str(node.get("z")), "none"),
                    "function_type": infer_function_type(text, project_type),
                    "parameter_name": _normalize_whitespace(parameter_name),
                    "canonical_semantic_key": point_record.get("canonical_semantic_key") if point_record else _canonical_semantic_key(
                        project_type=project_type,
                        tab_role=tab_roles.get(str(node.get("z")), "none"),
                        point_name=display_name,
                        node_type=str(node.get("type", "")),
                        role="parameter",
                    ),
                    "field": field,
                    "value": value,
                    "unit": _infer_unit(text, node),
                }
            )
    return samples


def _build_parameter_stats(parameter_samples_by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for (project_type, function_type, parameter_name, unit), samples in sorted(parameter_samples_by_key.items()):
        values = [float(sample["value"]) for sample in samples]
        if not values:
            continue
        records.append(
            {
                "stat_id": stable_id("stat", f"{project_type}:{function_type}:{parameter_name}:{unit}"),
                "project_type": project_type,
                "function_type": function_type,
                "parameter_name": parameter_name,
                "unit": unit or None,
                "samples": [
                    {
                        "template_id": sample["template_id"],
                        "source_path": sample["source_path"],
                        "node_id": sample["node_id"],
                        "tab_label": sample["tab_label"],
                        "value": sample["value"],
                    }
                    for sample in samples[:40]
                ],
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "median": median(values),
                "notes": ["仅来自历史模板统计，不等同于设计规范。"],
            }
        )
    return records


def _build_template_profile(
    *,
    path: Path,
    template_id: str,
    project_type: str,
    source_path: str,
    nodes: list[dict[str, Any]],
    tab_labels: dict[str, str],
    tab_roles: dict[str, str],
    configuration_records: list[dict[str, Any]],
) -> dict[str, Any]:
    node_type_counts = Counter(str(node.get("type", "")) for node in nodes)
    text = f"{' '.join(tab_labels.values())} {' '.join(_node_display_name(node) for node in nodes[:200])}"
    has_hard_io = bool({"hwInput", "hwOutput"} & set(node_type_counts))
    has_communication = any(_is_communication_type(node_type) for node_type in node_type_counts)
    if "示例" in path.name and not has_hard_io:
        archetype = "logic_example"
    elif "标准" in path.name or tab_roles and "configuration" in set(tab_roles.values()):
        archetype = "engineering_standard"
    else:
        archetype = "project_instance"
    if has_hard_io and has_communication:
        io_binding_level = "mixed"
    elif has_hard_io:
        io_binding_level = "hard_io"
    elif has_communication:
        io_binding_level = "communication_bound"
    else:
        io_binding_level = "software_only"

    config_points = [point for record in configuration_records for point in record.get("config_points", [])]
    max_device_count = max((_safe_int(point.get("value")) or 0 for point in config_points if point.get("role") == "device_count"), default=0)
    max_device_count = max(max_device_count, _max_device_limit_from_nodes(nodes) or 0)
    return {
        "template_id": template_id,
        "project_type": project_type,
        "source_path": source_path,
        "file_name": path.name,
        "node_count": len(nodes),
        "tab_count": len(tab_labels),
        "tabs": [{"tab_id": tab_id, "tab_label": label, "tab_role": tab_roles.get(tab_id, "other")} for tab_id, label in sorted(tab_labels.items())],
        "template_archetype": archetype,
        "io_binding_level": io_binding_level,
        "configurability": {
            "has_configuration_tab": any(role == "configuration" for role in tab_roles.values()),
            "device_count_configurable": any(point.get("role") == "device_count" for point in config_points),
            "max_device_count": max_device_count or None,
        },
        "node_type_counts": dict(node_type_counts.most_common()),
    }


def _build_code_facts(indexes: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for source_name in [
        "template_profile_index",
        "tab_role_index",
        "point_identity_index",
        "subflow_index",
        "configuration_model_index",
        "bitmask_mapping_index",
        "algorithm_role_index",
        "control_chain_index",
        "protection_chain_index",
        "parameter_stat_index",
    ]:
        for item in indexes.get(source_name, []):
            title = str(
                item.get("point_name")
                or item.get("name")
                or item.get("tab_label")
                or item.get("parameter_name")
                or item.get("file_name")
                or item.get("chain_type")
                or source_name
            )
            facts.append(
                {
                    "fact_id": stable_id("fact", f"{source_name}:{item.get('template_id', '')}:{item.get('record_id') or item.get('point_id') or item.get('chain_id') or item.get('stat_id') or title}"),
                    "source_index": source_name,
                    "unit_type": source_name.replace("_index", ""),
                    "template_id": item.get("template_id"),
                    "project_type": item.get("project_type"),
                    "source_path": item.get("source_path"),
                    "title": title,
                    "summary": _fact_summary(source_name, item),
                    "risk_tags": item.get("risk_tags", []),
                    "quality": {"extraction_method": "deterministic", "confidence": "medium", "review_status": "generated"},
                }
            )
    return facts


def _fact_summary(source_name: str, item: dict[str, Any]) -> str:
    if source_name == "template_profile_index":
        return f"{item.get('file_name')} 是 {item.get('template_archetype')}，IO 绑定级别 {item.get('io_binding_level')}。"
    if source_name == "tab_role_index":
        return f"{item.get('tab_label')} 页面角色为 {item.get('tab_role')}，节点数 {item.get('node_count')}。"
    if source_name == "point_identity_index":
        return f"{item.get('point_name')} 点位角色 {','.join(item.get('point_roles') or [])}。"
    if source_name == "subflow_index":
        return f"{item.get('record_type')} {item.get('name') or item.get('definition_name')}。"
    if source_name == "configuration_model_index":
        return f"{item.get('tab_label')} 配置模型包含 {len(item.get('config_points') or [])} 个配置点。"
    if source_name == "bitmask_mapping_index":
        device_array = item.get("device_array") or {}
        return f"{item.get('node_type')} 位组合，设备类型 {device_array.get('equipment_type')}，最大数量 {device_array.get('max_count')}。"
    if source_name in {"control_chain_index", "protection_chain_index"}:
        return str(item.get("summary") or "")
    if source_name == "parameter_stat_index":
        return f"{item.get('parameter_name')} 历史样本 {item.get('count')} 个，中位数 {item.get('median')}。"
    return str(item)


def _build_graph_indexes(
    node_by_id: dict[str, dict[str, Any]]
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, list[dict[str, Any]]]]:
    upstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    downstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    input_refs_by_node: dict[str, list[dict[str, Any]]] = {}
    for target_id, node in node_by_id.items():
        refs = list(iter_input_refs(node.get("wires")))
        input_refs_by_node[target_id] = refs
        for ref in refs:
            source_id = ref["source_id"]
            if source_id in node_by_id:
                upstream_by_node[target_id].add(source_id)
                downstream_by_node[source_id].add(target_id)
    return upstream_by_node, downstream_by_node, input_refs_by_node


def iter_input_refs(wires: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if not isinstance(wires, list):
        return refs
    for input_port, input_sources in enumerate(wires):
        source_items = input_sources if isinstance(input_sources, list) else [input_sources]
        for source in source_items:
            if isinstance(source, str):
                refs.append({"source_id": source, "source_port": 0, "target_input_port": input_port})
            elif isinstance(source, dict) and isinstance(source.get("id"), str):
                refs.append(
                    {
                        "source_id": source["id"],
                        "source_port": int(source.get("port") or 0),
                        "target_input_port": input_port,
                    }
                )
    return refs


def _subflow_ports(value: Any, *, direction: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    ports: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"{direction}_{index}")
        ports.append(
            {
                "index": index,
                "name": name,
                "role": infer_point_roles(name, "subflow_port")[0],
                "wire_refs": [
                    {"node_id": wire.get("id"), "port": int(wire.get("port") or 0)}
                    for wire in item.get("wires", [])
                    if isinstance(wire, dict) and isinstance(wire.get("id"), str)
                ],
            }
        )
    return ports


def _instance_input_bindings(node_id: str, input_refs_by_node: dict[str, list[dict[str, Any]]], node_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for ref in input_refs_by_node.get(node_id, []):
        source_node = node_by_id.get(ref["source_id"])
        bindings.append(
            {
                "input_port": ref["target_input_port"],
                "source_node_id": ref["source_id"],
                "source_port": ref["source_port"],
                "source_name": _node_display_name(source_node) if source_node else ref["source_id"],
            }
        )
    return bindings[:80]


def _instance_output_bindings(node_id: str, downstream_by_node: dict[str, set[str]], node_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for target_id in sorted(downstream_by_node.get(node_id, set())):
        target_node = node_by_id.get(target_id)
        bindings.append({"target_node_id": target_id, "target_name": _node_display_name(target_node) if target_node else target_id})
    return bindings[:80]


def _node_display_name(node: dict[str, Any] | None) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "quote":
        parsed = parse_quote_label(str(node.get("labelName") or ""))
        if parsed.get("display"):
            return str(parsed["display"])
    for key in ("label", "objectName", "objctName", "topic", "name", "labelName", "bacnetObjectPrefix"):
        value = node.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in GENERIC_NODE_NAMES:
            return value.strip()
    name = node.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    node_id = node.get("id")
    return str(node_id) if node_id is not None else ""


def _node_search_text(node: dict[str, Any] | None, tab_label: str = "") -> str:
    if not isinstance(node, dict):
        return ""
    values = [tab_label, _node_display_name(node), str(node.get("type") or "")]
    for key in ("labelName", "info", "objectName", "objctName", "topic", "bacnetObjectPrefix", "swInputType", "as", "lType"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return _normalize_whitespace(" ".join(values))


def infer_equipment(text: str) -> list[str]:
    equipment: list[str] = []
    for label, keywords in [
        ("送风机", ["送风机", "风机"]),
        ("新风阀", ["新风阀", "新风"]),
        ("回风阀", ["回风阀", "回风"]),
        ("排风机", ["排风机", "排风"]),
        ("直膨机", ["直膨"]),
        ("主机", ["主机", "冷机"]),
        ("热泵", ["热泵", "机组"]),
        ("水泵", ["水泵", "冷冻泵", "冷却泵"]),
        ("冷却塔", ["冷却塔"]),
        ("旁通阀", ["旁通阀", "旁通"]),
        ("蝶阀", ["蝶阀"]),
    ]:
        if any(keyword in text for keyword in keywords):
            equipment.append(label)
    return sorted(set(equipment))


def infer_equipment_type(text: str) -> str:
    for equipment_type, keywords in [
        ("heat_pump", ["热泵", "机组"]),
        ("pump", ["水泵", "冷冻泵", "冷却泵", "泵"]),
        ("chiller", ["主机", "冷机"]),
        ("cooling_tower", ["冷却塔"]),
        ("valve", ["阀", "蝶阀", "旁通"]),
        ("fan", ["风机"]),
    ]:
        if any(keyword in text for keyword in keywords):
            return equipment_type
    return "unknown"


def infer_scope(text: str) -> str:
    for scope, keywords in SCOPE_RULES:
        if any(keyword in text for keyword in keywords):
            return scope
    return "system"


def _infer_config_role(text: str) -> str | None:
    for role, keywords in CONFIG_ROLE_RULES:
        if any(keyword in text for keyword in keywords):
            return role
    return None


def _configuration_affects(config_points: list[dict[str, Any]]) -> list[str]:
    affects: set[str] = set()
    for point in config_points:
        role = point.get("role")
        scope = point.get("scope")
        if role == "device_count":
            affects.add("设备轮询启停")
            affects.add("设备需求数")
            affects.add("通讯和点位映射")
        if role == "topology":
            affects.add("设备联动顺序")
            affects.add("水泵和阀门拓扑")
        if role == "mode":
            affects.add("制冷制热模式")
            affects.add("温度和压差控制路径")
        if scope == "pump":
            affects.add("水泵频率温压双控")
    return sorted(affects)


def _communication_metadata(node: dict[str, Any]) -> dict[str, Any]:
    node_type = str(node.get("type", ""))
    return {
        "is_communication": _is_communication_type(node_type) or bool(node.get("bacnetVisible")) or bool(node.get("modbusSlaveEnable")),
        "node_type": node_type,
        "bacnet_visible": node.get("bacnetVisible"),
        "bacnet_object_type": node.get("bacnetObjectType"),
        "bacnet_object_instance": node.get("bacnetObjectInstance"),
        "modbus_slave_enable": node.get("modbusSlaveEnable"),
        "modbus_slave_reg_addr": node.get("modbusSlaveRegAddr"),
        "modbus_slave_reg_type": node.get("modbusSlaveRegType"),
    }


def _is_communication_type(node_type: str) -> bool:
    lowered = node_type.lower()
    return "modbus" in lowered or "bac" in lowered or "mqtt" in lowered


def _canonical_semantic_key(*, project_type: str, tab_role: str, point_name: str, node_type: str, role: str) -> str:
    normalized_name = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", point_name.strip()).strip("_")
    if not normalized_name:
        normalized_name = "unnamed"
    return f"{project_type}.{tab_role}.{role}.{normalized_name}.{node_type}"


def _bounded_neighborhood(
    anchor_id: str,
    upstream_by_node: dict[str, set[str]],
    downstream_by_node: dict[str, set[str]],
    *,
    max_depth: int,
    limit: int,
) -> list[str]:
    selected: list[str] = []
    seen = {anchor_id}
    queue = [(anchor_id, 0)]
    while queue and len(selected) < limit:
        node_id, depth = queue.pop(0)
        selected.append(node_id)
        if depth >= max_depth:
            continue
        neighbors = sorted(upstream_by_node.get(node_id, set()) | downstream_by_node.get(node_id, set()))
        for neighbor in neighbors:
            if neighbor in seen:
                continue
            seen.add(neighbor)
            queue.append((neighbor, depth + 1))
    return selected


def _downstream_closure(anchor_id: str, downstream_by_node: dict[str, set[str]], *, max_depth: int, limit: int) -> list[str]:
    selected: list[str] = []
    seen = {anchor_id}
    queue = [(anchor_id, 0)]
    while queue and len(selected) < limit:
        node_id, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for target in sorted(downstream_by_node.get(node_id, set())):
            if target in seen:
                continue
            seen.add(target)
            selected.append(target)
            queue.append((target, depth + 1))
            if len(selected) >= limit:
                break
    return selected


def _neighborhood_text(
    node_id: str,
    node_by_id: dict[str, dict[str, Any]],
    upstream_by_node: dict[str, set[str]],
    downstream_by_node: dict[str, set[str]],
) -> str:
    ids = _bounded_neighborhood(node_id, upstream_by_node, downstream_by_node, max_depth=1, limit=16)
    return _normalize_whitespace(" ".join(_node_search_text(node_by_id.get(item)) for item in ids))


def _chain_node_refs(node_ids: list[str], node_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for node_id in node_ids[:24]:
        node = node_by_id.get(node_id)
        if node is None:
            continue
        refs.append({"id": node_id, "type": node.get("type"), "name": _node_display_name(node), "role": infer_point_roles(_node_search_text(node), str(node.get("type", "")))[0]})
    return refs


def _control_chain_type(node_type: str, text: str) -> str:
    if node_type == "pid":
        return "sensor_to_pid"
    if "设定" in text or "SP" in text:
        return "setpoint_to_output"
    if "反馈" in text or "状态" in text:
        return "command_to_feedback"
    return "control_logic_chain"


def _risk_tags_for_text(text: str) -> set[str]:
    tags: set[str] = set()
    if any(keyword in text for keyword in ["防冻", "故障", "保护", "联锁", "急停"]):
        tags.add("protection_logic")
    if any(keyword in text for keyword in ["输出", "命令", "阀", "风机", "水泵", "主机", "热泵"]):
        tags.add("actuator_logic")
    if any(keyword in text for keyword in ["通讯", "Modbus", "BACnet", "MQTT"]):
        tags.add("communication_mapping")
    if any(keyword in text for keyword in ["IO", "硬接", "点位"]):
        tags.add("io_point_mapping")
    if any(keyword in text for keyword in ["CO2", "新风"]):
        tags.add("air_quality_logic")
    if any(keyword in text for keyword in ["数量", "台数", "位组合"]):
        tags.add("device_count_topology")
    return tags


def _numeric_parameter_values(node: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for field in [
        "swInputDefault",
        "constant",
        "outOfServiceValue",
        "proportional",
        "integral",
        "derivative",
        "highPidOutLimit",
        "lowPidOutLimit",
        "interval",
        "deadBand",
    ]:
        value = node.get(field)
        numeric = _safe_float(value)
        if numeric is not None:
            values[field] = numeric
    return values


def _infer_unit(text: str, node: dict[str, Any]) -> str | None:
    units = node.get("bacnetObjectUnits")
    if isinstance(units, str) and units and units != "No Units":
        return units
    if "CO2" in text or "二氧化碳" in text:
        return "ppm"
    if "温度" in text:
        return "degC"
    if "湿度" in text:
        return "%"
    if "频率" in text:
        return "Hz"
    if "开度" in text or "比例" in text:
        return "%"
    return None


def _bitmask_chain_type(node: dict[str, Any], bit_mapping: list[dict[str, Any]]) -> str:
    text = " ".join(str(item.get("source_name", "")) for item in bit_mapping)
    if "轮" in text or "优先" in text:
        return "device_rotation_chain"
    if "运行" in text or "故障" in text or "可用" in text:
        return "device_array_status_chain"
    return "bitmask_mapping_chain"


def _node_bit_value(node: dict[str, Any], fallback: int) -> int:
    value = node.get("bit")
    numeric = _safe_int(value)
    if numeric is not None:
        return numeric
    return fallback


def _dominant_equipment_type(bit_mapping: list[dict[str, Any]]) -> str:
    counts = Counter(str(item.get("equipment_type") or "unknown") for item in bit_mapping)
    if not counts:
        return "unknown"
    return counts.most_common(1)[0][0]


def _infer_device_no(text: str) -> int | None:
    match = re.search(r"([0-9]+)\s*号", text)
    if match:
        return int(match.group(1))
    return None


def _first_existing_value(node: dict[str, Any], fields: list[str]) -> Any:
    for field in fields:
        if field in node:
            return node[field]
    return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_device_limit_from_nodes(nodes: list[dict[str, Any]]) -> int | None:
    values: list[int] = []
    for node in nodes:
        text = _node_display_name(node)
        for pattern in [r"上限\s*([0-9]+)", r"最多\s*([0-9]+)\s*台", r"最大\s*([0-9]+)\s*台"]:
            values.extend(int(match) for match in re.findall(pattern, text))
    return max(values) if values else None


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    data = build_advisory_kb()
    write_advisory_kb(data)
    print(f"已生成咨询知识库 manifest: {relative_posix(ADVISORY_INDEX_DIR / 'advisory_kb_manifest.json')}")
    for name, count in sorted(data["manifest"]["row_counts"].items()):
        print(f"- {name}: {count}")


if __name__ == "__main__":
    main()
