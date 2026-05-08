from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.build_advisory_kb import build_advisory_kb, iter_input_refs, write_advisory_kb


@pytest.fixture(scope="module")
def advisory_kb() -> dict[str, Any]:
    return build_advisory_kb()


def test_build_advisory_kb_outputs_required_indexes(advisory_kb: dict[str, Any]) -> None:
    manifest = advisory_kb["manifest"]
    indexes = advisory_kb["indexes"]

    assert manifest["source_count"] == 5
    assert manifest["row_counts"]["template_profile_index"] == 5
    assert manifest["row_counts"]["tab_role_index"] >= 30
    assert manifest["row_counts"]["point_identity_index"] > 3000
    assert manifest["row_counts"]["quote_reference_index"] > 2000
    assert manifest["row_counts"]["subflow_index"] > 80
    assert manifest["row_counts"]["parameter_stat_index"] > 300
    assert set(indexes) >= {
        "template_profile_index",
        "tab_role_index",
        "point_identity_index",
        "quote_reference_index",
        "point_usage_index",
        "subflow_index",
        "configuration_model_index",
        "bitmask_mapping_index",
        "algorithm_role_index",
        "control_chain_index",
        "protection_chain_index",
        "parameter_stat_index",
        "code_facts",
    }


def test_template_profiles_classify_template_archetype_and_topology(advisory_kb: dict[str, Any]) -> None:
    profiles = advisory_kb["indexes"]["template_profile_index"]
    standard = _find_one(profiles, lambda item: "风冷热泵标准控制程序" in item["file_name"])
    logic_example = _find_one(profiles, lambda item: "机房群控示例程序" in item["file_name"])
    ahu_profile = _find_one(profiles, lambda item: item["project_type"] == "ahu")

    assert standard["template_archetype"] == "engineering_standard"
    assert standard["io_binding_level"] == "mixed"
    assert standard["configurability"] == {
        "has_configuration_tab": True,
        "device_count_configurable": True,
        "max_device_count": 6,
    }
    assert any(tab["tab_label"] == "使用配置" and tab["tab_role"] == "configuration" for tab in standard["tabs"])
    assert any(tab["tab_label"] == "旁通阀控制" and tab["tab_role"] == "bypass_valve_control" for tab in standard["tabs"])
    assert any(tab["tab_label"] == "水泵控制" and tab["tab_role"] == "equipment_standard_control" for tab in standard["tabs"])

    assert logic_example["template_archetype"] == "logic_example"
    assert logic_example["io_binding_level"] == "software_only"
    assert logic_example["configurability"]["max_device_count"] is None
    assert any(tab["tab_label"] == "主机及其蝶阀标准控制" and tab["tab_role"] == "equipment_standard_control" for tab in logic_example["tabs"])
    assert ahu_profile["configurability"]["max_device_count"] is None


def test_configuration_model_extracts_topology_points(advisory_kb: dict[str, Any]) -> None:
    configs = advisory_kb["indexes"]["configuration_model_index"]
    assert len(configs) == 1
    config = configs[0]
    points = config["config_points"]

    assert config["tab_label"] == "使用配置"
    assert config["system_type"] == "air_source_heat_pump"
    assert any(point["name"] == "热泵总数量" and point["role"] == "device_count" and point["scope"] == "heat_pump" for point in points)
    assert any(point["name"] == "水泵总数量" and point["role"] == "device_count" and point["scope"] == "pump" for point in points)
    assert any(point["name"] == "泵连形式" and point["role"] == "topology" for point in points)
    assert "设备轮询启停" in config["affects"]
    assert "水泵和阀门拓扑" in config["affects"]


def test_quote_references_resolve_to_original_nodes(advisory_kb: dict[str, Any]) -> None:
    quotes = advisory_kb["indexes"]["quote_reference_index"]
    resolved = [quote for quote in quotes if quote["resolved"]]

    assert len(resolved) > 2000
    assert all(quote["referenced_node_id"] for quote in resolved[:50])
    assert any(
        quote["referenced_display"] == "送风机运行状态"
        and quote["canonical_semantic_key"].startswith("ahu.")
        for quote in resolved
    )


def test_subflow_definitions_and_instances_are_separated(advisory_kb: dict[str, Any]) -> None:
    subflows = advisory_kb["indexes"]["subflow_index"]
    definitions = [item for item in subflows if item["record_type"] == "subflow_definition"]
    instances = [item for item in subflows if item["record_type"] == "subflow_instance"]
    co2_definition = _find_one(definitions, lambda item: "CO2控制" in item["name"])
    fan_definition = _find_one(definitions, lambda item: "送风机标准控制" in item["name"])

    assert definitions
    assert instances
    assert co2_definition["status_in_template"] == "defined_only"
    assert co2_definition["instance_count"] == 0
    assert any(port["name"] == "回风CO2浓度设定值" and port["role"] == "setpoint" for port in co2_definition["input_ports"])
    assert fan_definition["status_in_template"] == "instantiated"
    assert fan_definition["instance_count"] >= 1
    assert all("input_bindings" in item and "output_bindings" in item for item in instances)


def test_bitmask_and_algorithm_indexes_capture_engineering_semantics(advisory_kb: dict[str, Any]) -> None:
    bitmasks = advisory_kb["indexes"]["bitmask_mapping_index"]
    algorithms = advisory_kb["indexes"]["algorithm_role_index"]
    heat_pump_bitmask = _find_one(
        bitmasks,
        lambda item: item["device_array"]["equipment_type"] == "heat_pump"
        and item["device_array"]["max_count"] >= 6,
    )

    assert heat_pump_bitmask["device_array"]["active_count_source"] == "configuration"
    assert any(mapping["device_no"] == 1 and "1号热泵" in mapping["source_name"] for mapping in heat_pump_bitmask["device_array"]["bit_mapping"])
    assert any(item["algorithm_role"] == "wet_bulb_temperature" for item in algorithms)
    assert any(item["algorithm_role"] == "enthalpy_calculation" for item in algorithms)
    assert any(item["algorithm_role"] == "pid_control" and item["function_type"] == "bypass_valve_control" for item in algorithms)


def test_iter_input_refs_preserves_input_and_source_ports() -> None:
    refs = iter_input_refs([[{"id": "a", "port": 2}], ["b"], {"id": "c"}])

    assert refs == [
        {"source_id": "a", "source_port": 2, "target_input_port": 0},
        {"source_id": "b", "source_port": 0, "target_input_port": 1},
        {"source_id": "c", "source_port": 0, "target_input_port": 2},
    ]


def test_write_advisory_kb_outputs_manifest_and_jsonl(tmp_path: Path, advisory_kb: dict[str, Any]) -> None:
    write_advisory_kb(advisory_kb, output_dir=tmp_path)

    manifest = json.loads((tmp_path / "advisory_kb_manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_counts"]["template_profile_index"] == 5
    assert (tmp_path / "point_identity_index.jsonl").exists()
    assert (tmp_path / "quote_reference_index.jsonl").exists()
    assert (tmp_path / "configuration_model_index.jsonl").exists()


def _find_one(items: list[dict[str, Any]], predicate: Any) -> dict[str, Any]:
    for item in items:
        if predicate(item):
            return item
    raise AssertionError("未找到符合条件的记录")
