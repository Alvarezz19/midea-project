from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.json_project import (
    create_project_version,
    create_project_version_from_nodes,
    find_nodes,
    get_project_version,
    list_project_versions,
    load_project,
    save_project,
    summarize_project,
)
from app.services.knowledge import get_knowledge_context, load_knowledge_chunks, search_knowledge
from app.services.patch_engine import PatchEngineError, apply_patch, dry_run_patch
from app.services.planner import plan_patch_request
from app.services.project_diff import ProjectDiffError, diff_project_versions
from app.services.retrieval import (
    RetrievalError,
    get_template_by_id,
    load_block_context,
    load_block_index,
    load_node_neighborhood,
    search_blocks,
    search_nodes,
    search_tabs,
    search_templates,
)
from app.services.validator import validate_project


PLANT_TEMPLATE = Path("programs/机房群控程序/风冷热泵标准控制程序[风冷涡旋]20240905.json")
AHU_TEMPLATE = Path("programs/AHU程序/泰安宁阳中医院/flows_20260206160555.json")


def test_existing_templates_validate_cleanly() -> None:
    paths = sorted(Path("programs").rglob("*.json"))
    assert len(paths) == 5
    for path in paths:
        report = validate_project(load_project(path))
        assert report["valid"], path
        assert report["exportable"], path
        assert report["blocked_export_reasons"] == []
        assert report["error_count"] == 0
        assert report["warning_count"] == 0


def test_project_summary_and_find_nodes() -> None:
    nodes = load_project(PLANT_TEMPLATE)
    summary = summarize_project(nodes)

    assert summary["node_count"] == 3450
    assert summary["tab_count"] == 9
    assert summary["node_type_counts"]["compare"] == 145

    matches = find_nodes(nodes, {"tab_label_contains": "水泵", "type": "compare"})
    assert len(matches) == 1
    assert matches[0]["id"] == "3a4c97e"


def test_create_project_version_uses_temp_directory(tmp_path: Path) -> None:
    metadata = create_project_version(
        AHU_TEMPLATE,
        project_id="test_project",
        version_id="v_test",
        versions_dir=tmp_path,
        note="自动化测试",
    )

    version_path = Path(metadata["version_path"])
    assert version_path.exists()
    assert metadata["project_id"] == "test_project"
    assert metadata["version_id"] == "v_test"
    assert metadata["parent_version_id"] is None
    assert len(metadata["json_sha256"]) == 64
    assert validate_project(load_project(version_path))["valid"]


def test_create_child_project_version_does_not_overwrite_parent(tmp_path: Path) -> None:
    parent = create_project_version(
        PLANT_TEMPLATE,
        project_id="versioned_project",
        version_id="v_parent",
        versions_dir=tmp_path,
        note="父版本",
    )
    parent_path = Path(parent["version_path"])
    parent_nodes = load_project(parent_path)
    child_nodes = load_project(parent_path)
    find_nodes(child_nodes, {"id": "3a4c97e"})[0]["name"] = "子版本-水泵比较节点"

    child = create_project_version_from_nodes(
        child_nodes,
        project_id="versioned_project",
        parent_version_id="v_parent",
        version_id="v_child",
        versions_dir=tmp_path,
        validation_report={"valid": True, "error_count": 0, "warning_count": 0},
        patch_summary={"changed": True},
    )

    assert child["parent_version_id"] == "v_parent"
    assert child["version_path"] != parent["version_path"]
    assert find_nodes(load_project(parent_path), {"id": "3a4c97e"})[0]["name"] == find_nodes(parent_nodes, {"id": "3a4c97e"})[0].get("name")
    assert find_nodes(load_project(child["version_path"]), {"id": "3a4c97e"})[0]["name"] == "子版本-水泵比较节点"
    assert [item["version_id"] for item in list_project_versions("versioned_project", versions_dir=tmp_path)] == ["v_parent", "v_child"]
    assert get_project_version("versioned_project", "v_child", versions_dir=tmp_path)["parent_version_id"] == "v_parent"


def test_project_diff_between_versions_returns_node_summary(tmp_path: Path) -> None:
    parent = create_project_version(
        PLANT_TEMPLATE,
        project_id="diff_project",
        version_id="v_parent",
        versions_dir=tmp_path,
    )
    child_nodes = load_project(parent["version_path"])
    original_name = find_nodes(child_nodes, {"id": "3a4c97e"})[0].get("name")
    find_nodes(child_nodes, {"id": "3a4c97e"})[0]["name"] = "版本diff-水泵比较节点"
    create_project_version_from_nodes(
        child_nodes,
        project_id="diff_project",
        parent_version_id="v_parent",
        version_id="v_child",
        versions_dir=tmp_path,
        validation_report={"valid": True, "error_count": 0, "warning_count": 0},
    )

    result = diff_project_versions("diff_project", "v_parent", "v_child", versions_dir=tmp_path)

    assert result["from_version"]["version_id"] == "v_parent"
    assert result["to_version"]["version_id"] == "v_child"
    assert result["diff"]["summary"] == {
        "added_count": 0,
        "removed_count": 0,
        "modified_count": 1,
        "affected_node_count": 1,
    }
    assert result["diff"]["affected_node_ids"] == ["3a4c97e"]
    assert result["diff"]["modified"][0]["field_changes"] == [
        {"field": "name", "old_value": original_name, "new_value": "版本diff-水泵比较节点"}
    ]

    with pytest.raises(ProjectDiffError, match="不能相同"):
        diff_project_versions("diff_project", "v_child", "v_child", versions_dir=tmp_path)


def test_retrieval_template_tab_node_and_neighborhood() -> None:
    templates = search_templates("风冷热泵 水泵 旁通阀", project_type="plant_room", limit=1)
    assert templates
    best = templates[0]
    assert best["file_name"] == "风冷热泵标准控制程序[风冷涡旋]20240905.json"
    assert get_template_by_id(best["template_id"])["source_path"] == best["source_path"]

    tabs = search_tabs("水泵控制", template_id=best["template_id"], limit=3)
    assert tabs
    assert tabs[0]["tab_label"] == "水泵控制"

    nodes = search_nodes("水泵 比较判断", template_id=best["template_id"], tab_label_contains="水泵", node_type="compare", limit=3)
    assert nodes
    assert nodes[0]["node_id"] == "3a4c97e"

    neighborhood = load_node_neighborhood(best["source_path"], [nodes[0]["node_id"]], depth=1, max_nodes=30)
    assert neighborhood["node_count"] == 4
    assert neighborhood["edge_count"] == 3
    assert not neighborhood["truncated"]


def test_retrieval_searches_function_blocks() -> None:
    best = search_templates("风冷热泵 水泵 旁通阀", project_type="plant_room", limit=1)[0]

    pump_blocks = search_blocks("水泵控制", template_id=best["template_id"], limit=3)
    assert pump_blocks
    assert pump_blocks[0]["function_type"] == "pump_control"
    assert pump_blocks[0]["tab_label"] == "水泵控制"
    assert pump_blocks[0]["node_ids"]
    assert pump_blocks[0]["anchor_node_ids"]
    assert pump_blocks[0]["node_count"] == len(pump_blocks[0]["node_ids"])

    bypass_blocks = search_blocks("旁通阀 压差", template_id=best["template_id"], limit=3)
    assert bypass_blocks
    assert bypass_blocks[0]["function_type"] == "bypass_valve_control"
    assert "control_loop" in bypass_blocks[0]["risk_tags"]

    ahu_blocks = search_blocks("排风机联动", project_type="ahu", limit=5)
    assert ahu_blocks
    assert ahu_blocks[0]["function_type"] == "exhaust_fan_linkage"


def test_retrieval_loads_block_context_with_budget() -> None:
    best = search_templates("风冷热泵 水泵 旁通阀", project_type="plant_room", limit=1)[0]
    block = search_blocks("旁通阀 压差", template_id=best["template_id"], limit=1)[0]

    context = load_block_context(block["block_id"], max_nodes=12, max_chars=12000)

    assert context["block"]["block_id"] == block["block_id"]
    assert context["block"]["function_type"] == "bypass_valve_control"
    assert context["source_path"] == best["source_path"]
    assert 1 <= len(context["nodes"]) <= 12
    assert all("raw" not in node for node in context["nodes"])
    assert all(set(node).issubset({"id", "type", "z", "name", "label", "inputs", "outputs", "x", "y", "wires", "fixedValue", "outOfServiceValue", "tripPoint", "as"}) for node in context["nodes"])
    selected_ids = {node["id"] for node in context["nodes"]}
    assert all(edge["source"] in selected_ids and edge["target"] in selected_ids for edge in context["internal_edges"])
    assert "inbound_edges" in context["boundary"]
    assert "outbound_edges" in context["boundary"]
    assert context["budget"]["max_nodes"] == 12
    assert context["budget"]["estimated_chars"] <= 12000

    tiny_context = load_block_context(block["block_id"], max_nodes=3, max_chars=1600)
    assert len(tiny_context["nodes"]) <= 3
    assert tiny_context["budget"]["truncated_by_node_count"] or tiny_context["budget"]["truncated_by_chars"]

    raw_context = load_block_context(block["block_id"], max_nodes=1, max_chars=6000, include_raw_nodes=True)
    assert "raw" in raw_context["nodes"][0]


def test_retrieval_reports_missing_node() -> None:
    with pytest.raises(RetrievalError, match="锚点节点不存在"):
        load_node_neighborhood(PLANT_TEMPLATE, ["missing-node"], depth=1)

    with pytest.raises(RetrievalError, match="功能块不存在"):
        load_block_context("missing-block-id")


def test_patch_engine_updates_renames_comments_and_validates(tmp_path: Path) -> None:
    nodes = load_project(PLANT_TEMPLATE)
    patch = {
        "operations": [
            {"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": "测试-水泵比较节点"},
            {"op": "update_param", "node_selector": {"id": "67febfa"}, "params": {"fixedValue": "5", "outOfServiceValue": 5}},
            {"op": "add_comment", "tab_selector": {"label_contains": "水泵控制"}, "text": "测试备注", "x": 160, "y": 120},
        ]
    }

    result = apply_patch(nodes, patch)
    assert result["changed"]
    assert len(result["changes"]) == 4
    assert validate_project(result["nodes"])["valid"]

    target = tmp_path / "patched.json"
    save_project(target, result["nodes"])
    reloaded = load_project(target)
    assert validate_project(reloaded)["valid"]
    assert find_nodes(reloaded, {"id": "3a4c97e"})[0]["name"] == "测试-水泵比较节点"
    assert find_nodes(reloaded, {"id": "67febfa"})[0]["fixedValue"] == "5"


def test_patch_engine_replaces_constant_value_with_dry_run_diff() -> None:
    nodes = load_project(PLANT_TEMPLATE)

    result = dry_run_patch(nodes, {"op": "replace_constant", "node_selector": {"id": "67febfa"}, "value": "6"})

    assert result["saved"] is False
    assert result["valid"] is True
    assert result["changes"] == [
        {
            "op": "replace_constant",
            "node_id": "67febfa",
            "field": "fixedValue",
            "old_value": "4",
            "new_value": "6",
        }
    ]
    assert result["diff"]["summary"] == {
        "added_count": 0,
        "removed_count": 0,
        "modified_count": 1,
        "affected_node_count": 1,
    }
    assert result["diff"]["affected_node_ids"] == ["67febfa"]
    assert find_nodes(nodes, {"id": "67febfa"})[0]["fixedValue"] == "4"


def test_patch_engine_enables_aux_dynamic_input() -> None:
    nodes = load_project(PLANT_TEMPLATE)
    target = find_nodes(nodes, {"id": "f22a5df"})[0]
    assert target["type"] == "compare"
    assert target["inputAuxEnable"] is False
    assert target["inputs"] == 1

    result = dry_run_patch(nodes, {"op": "enable_dynamic_input", "node_selector": {"id": "f22a5df"}})

    assert result["valid"] is True
    assert result["changed"] is True
    updated = find_nodes(result["nodes"], {"id": "f22a5df"})[0]
    assert updated["inputAuxEnable"] is True
    assert updated["inputs"] == 2
    assert updated["wires"] == [[{"id": "1f65cd8", "port": 0}], []]
    assert result["diff"]["summary"] == {
        "added_count": 0,
        "removed_count": 0,
        "modified_count": 1,
        "affected_node_count": 1,
    }
    assert find_nodes(nodes, {"id": "f22a5df"})[0]["inputs"] == 1


def test_patch_engine_enables_pid_dynamic_input_option() -> None:
    nodes = load_project(AHU_TEMPLATE)
    target = find_nodes(nodes, {"id": "a2f6cc2"})[0]
    assert target["type"] == "pid"
    assert "highPidOutLimit" not in target["inputsOption"]
    assert target["inputs"] == 8

    result = dry_run_patch(
        nodes,
        {"op": "enable_dynamic_input", "node_selector": {"id": "a2f6cc2"}, "input_option": "highPidOutLimit"},
    )

    assert result["valid"] is True
    updated = find_nodes(result["nodes"], {"id": "a2f6cc2"})[0]
    assert updated["inputsOption"] == ["proportional", "integral", "derivative", "interval", "deadBand", "highPidOutLimit"]
    assert updated["inputs"] == 9
    assert len(updated["wires"]) == 9
    assert updated["wires"][-1] == []


def test_patch_engine_dry_run_returns_diff_without_mutating_source() -> None:
    nodes = load_project(PLANT_TEMPLATE)
    original_name = find_nodes(nodes, {"id": "3a4c97e"})[0].get("name")

    result = dry_run_patch(nodes, {"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": "dry-run-水泵比较节点"})

    assert result["saved"] is False
    assert result["valid"] is True
    assert result["changed"] is True
    assert result["diff"]["summary"] == {
        "added_count": 0,
        "removed_count": 0,
        "modified_count": 1,
        "affected_node_count": 1,
    }
    assert result["diff"]["affected_node_ids"] == ["3a4c97e"]
    assert result["diff"]["modified"][0]["field_changes"] == [
        {"field": "name", "old_value": original_name, "new_value": "dry-run-水泵比较节点"}
    ]
    assert find_nodes(nodes, {"id": "3a4c97e"})[0].get("name") == original_name


def test_patch_engine_adds_schema_node_and_updates_explicit_wires() -> None:
    nodes = load_project(PLANT_TEMPLATE)
    result = apply_patch(
        nodes,
        {
            "op": "add_node_from_schema",
            "module_type": "constInput",
            "tab_selector": {"label": "水泵控制"},
            "params": {"user_defined_name": "测试常量节点", "fixedValue": 9},
            "x": 220,
            "y": 260,
        },
    )

    assert result["changed"]
    added = result["changes"][0]
    assert added["op"] == "add_node_from_schema"
    assert added["type"] == "constInput"
    assert added["tab_id"] == "73b96a8"

    added_node = find_nodes(result["nodes"], {"id": added["node_id"]})[0]
    assert added_node["name"] == "测试常量节点"
    assert added_node["fixedValue"] == 9
    assert added_node["inputs"] == 0
    assert added_node["outputs"] == 1
    assert added_node["wires"] == []
    assert validate_project(result["nodes"])["valid"]

    connect_patch = {
        "op": "connect",
        "source_node_selector": {"id": added["node_id"]},
        "source_output": 0,
        "target_node_selector": {"id": "3a4c97e"},
        "target_input": 1,
    }
    connected = apply_patch(result["nodes"], connect_patch)
    assert connected["changes"] == [
        {
            "op": "connect",
            "source_node_id": added["node_id"],
            "source_output": 0,
            "target_node_id": "3a4c97e",
            "target_input": 1,
        }
    ]
    compare = find_nodes(connected["nodes"], {"id": "3a4c97e"})[0]
    assert {"id": added["node_id"], "port": 0} in compare["wires"][1]
    assert validate_project(connected["nodes"])["valid"]

    duplicate = apply_patch(connected["nodes"], connect_patch)
    assert not duplicate["changed"]

    disconnected = apply_patch(
        connected["nodes"],
        {
            "op": "disconnect",
            "source_node_selector": {"id": added["node_id"]},
            "target_node_selector": {"id": "3a4c97e"},
            "target_input": 1,
        },
    )
    compare_after_disconnect = find_nodes(disconnected["nodes"], {"id": "3a4c97e"})[0]
    assert {"id": added["node_id"], "port": 0} not in compare_after_disconnect["wires"][1]
    assert validate_project(disconnected["nodes"])["valid"]


def test_patch_engine_generates_schema_node_with_array_defaults() -> None:
    nodes = load_project(AHU_TEMPLATE)
    result = apply_patch(
        nodes,
        {
            "op": "add_node_from_schema",
            "module_type": "pid",
            "tab_selector": {"label": "控制"},
            "params": {"inputsOption": ["deadBand"], "inputsCount": 4},
        },
    )

    added = find_nodes(result["nodes"], {"id": result["changes"][0]["node_id"]})[0]
    assert added["type"] == "pid"
    assert added["inputs"] == 4
    assert added["inputsOption"] == ["deadBand"]
    assert added["wires"] == [[], [], [], []]
    assert validate_project(result["nodes"])["valid"]


def test_patch_engine_rejects_unsafe_or_ambiguous_changes() -> None:
    nodes = load_project(PLANT_TEMPLATE)

    with pytest.raises(PatchEngineError, match="必须唯一"):
        apply_patch(nodes, {"op": "rename_node", "node_selector": {"type": "compare"}, "new_name": "不应执行"})

    with pytest.raises(PatchEngineError, match="不允许修改结构字段"):
        apply_patch(nodes, {"op": "update_param", "node_selector": {"id": "67febfa"}, "params": {"id": "bad"}})

    with pytest.raises(PatchEngineError, match="不允许新增未知参数"):
        apply_patch(nodes, {"op": "update_param", "node_selector": {"id": "67febfa"}, "params": {"unknownField": 1}})

    with pytest.raises(PatchEngineError, match="不支持节点类型"):
        apply_patch(nodes, {"op": "replace_constant", "node_selector": {"id": "73b96a8"}, "field": "fixedValue", "value": 1})

    with pytest.raises(PatchEngineError, match="不允许修改 compare 的字段"):
        apply_patch(nodes, {"op": "replace_constant", "node_selector": {"id": "3a4c97e"}, "field": "outOfServiceValue", "value": 1})

    with pytest.raises(PatchEngineError, match="需要 input_option"):
        apply_patch(nodes, {"op": "enable_dynamic_input", "node_selector": {"id": "c8d7d0"}})

    with pytest.raises(PatchEngineError, match="不允许 pid 的选项"):
        apply_patch(nodes, {"op": "enable_dynamic_input", "node_selector": {"id": "c8d7d0"}, "input_option": "badOption"})

    with pytest.raises(PatchEngineError, match="schema 未定义参数"):
        apply_patch(
            nodes,
            {
                "op": "add_node_from_schema",
                "module_type": "constInput",
                "tab_selector": {"label": "水泵控制"},
                "params": {"user_defined_name": "非法节点", "fixedValue": 1, "unknownField": 1},
            },
        )

    with pytest.raises(PatchEngineError, match="target_input 超出"):
        apply_patch(
            nodes,
            {
                "op": "connect",
                "source_node_selector": {"id": "67febfa"},
                "target_node_selector": {"id": "3a4c97e"},
                "target_input": 99,
            },
        )


def test_index_files_are_parseable() -> None:
    template_index = json.loads(Path("indexes/templates/template_index.json").read_text(encoding="utf-8"))
    assert len(template_index) == 5

    tab_lines = Path("indexes/tabs/tab_index.jsonl").read_text(encoding="utf-8").splitlines()
    node_lines = Path("indexes/nodes/node_index.jsonl").read_text(encoding="utf-8").splitlines()
    block_lines = Path("indexes/blocks/block_index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(tab_lines) == 31
    assert len(node_lines) == 8413
    assert len(block_lines) >= 100
    assert all(isinstance(json.loads(line), dict) for line in tab_lines[:5])
    assert all(isinstance(json.loads(line), dict) for line in node_lines[:5])
    assert all(isinstance(json.loads(line), dict) for line in block_lines[:5])
    first_node = json.loads(node_lines[0])
    assert "input_sources" in first_node
    assert "wires_to" not in first_node
    first_block = json.loads(block_lines[0])
    assert "block_id" in first_block
    assert "node_ids" in first_block
    assert "risk_tags" in first_block
    assert len(load_block_index()) == len(block_lines)


def test_knowledge_chunks_and_search() -> None:
    chunks = load_knowledge_chunks()
    assert len(chunks) >= 25
    assert any(chunk.source_path == "knowledge/AHU控制策略.md" for chunk in chunks)
    assert any(chunk.source_path == "knowledge/机房群控控制策略.md" for chunk in chunks)
    assert any(chunk.source_path == "knowledge/工程规范.md" for chunk in chunks)

    ahu_results = search_knowledge("AHU 防冻保护 新风阀", limit=3, chunks=chunks)
    assert ahu_results
    assert ahu_results[0]["source_path"] == "knowledge/AHU控制策略.md"
    assert "防冻" in (ahu_results[0]["title"] + ahu_results[0]["content"])

    plant_results = search_knowledge("机房群控 旁通阀 压差控制", limit=3, chunks=chunks)
    assert plant_results
    assert plant_results[0]["source_path"] == "knowledge/机房群控控制策略.md"
    assert "旁通阀" in (plant_results[0]["title"] + plant_results[0]["content"])

    standard_results = search_knowledge("唯一节点选择器 结构字段 wires", limit=3, chunks=chunks)
    assert standard_results
    assert standard_results[0]["source_path"] == "knowledge/工程规范.md"


def test_knowledge_context_is_bounded() -> None:
    context = get_knowledge_context("CO2 控制 风机运行 新风阀", limit=3, max_chars=900)
    assert "来源：" in context
    assert "CO2" in context
    assert len(context) <= 900


def test_planner_creates_rename_patch_by_node_id(tmp_path: Path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="planner_project", version_id="v1", versions_dir=tmp_path)

    result = plan_patch_request(
        "把节点 3a4c97e 改名为 planner测试-水泵比较节点",
        project_path=metadata["version_path"],
        template_id="plant_room_efb00c114dcb",
        project_type="plant_room",
    )

    assert result["status"] == "planned"
    assert result["pending_patch"] == {
        "op": "rename_node",
        "node_selector": {"id": "3a4c97e"},
        "new_name": "planner测试-水泵比较节点",
    }
    assert result["knowledge"]
    assert result["target_node"]["id"] == "3a4c97e"


def test_planner_creates_update_and_comment_patches(tmp_path: Path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="planner_project", version_id="v2", versions_dir=tmp_path)

    update = plan_patch_request("把节点 67febfa 的 fixedValue 改为 5", project_path=metadata["version_path"])
    assert update["status"] == "planned"
    assert update["pending_patch"] == {
        "op": "update_param",
        "node_selector": {"id": "67febfa"},
        "params": {"fixedValue": "5"},
    }

    comment = plan_patch_request("在水泵控制添加备注：planner 测试备注", project_path=metadata["version_path"])
    assert comment["status"] == "planned"
    assert comment["pending_patch"]["op"] == "add_comment"
    assert comment["pending_patch"]["tab_selector"] == {"id": "73b96a8"}
    assert comment["pending_patch"]["text"] == "planner 测试备注"


def test_planner_creates_replace_constant_patch_by_node_id(tmp_path: Path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="planner_project", version_id="v_replace_constant", versions_dir=tmp_path)

    result = plan_patch_request("把节点 67febfa 的常量值改为 6", project_path=metadata["version_path"])

    assert result["status"] == "planned"
    assert result["pending_patch"] == {
        "op": "replace_constant",
        "node_selector": {"id": "67febfa"},
        "field": "fixedValue",
        "value": "6",
    }
    dry_run = dry_run_patch(load_project(metadata["version_path"]), result["pending_patch"])
    assert dry_run["valid"]
    assert dry_run["diff"]["summary"]["modified_count"] == 1


def test_planner_creates_enable_dynamic_input_patch_by_node_id(tmp_path: Path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="planner_project", version_id="v_dynamic_input", versions_dir=tmp_path)

    result = plan_patch_request("启用节点 f22a5df 的动态阈值输入", project_path=metadata["version_path"])

    assert result["status"] == "planned"
    assert result["pending_patch"] == {"op": "enable_dynamic_input", "node_selector": {"id": "f22a5df"}}
    dry_run = dry_run_patch(load_project(metadata["version_path"]), result["pending_patch"])
    assert dry_run["valid"]
    assert find_nodes(dry_run["nodes"], {"id": "f22a5df"})[0]["inputs"] == 2


def test_planner_can_target_unique_node_by_tab_and_type(tmp_path: Path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="planner_project", version_id="v_tab_type", versions_dir=tmp_path)

    result = plan_patch_request("把水泵控制比较判断的阈值改为 3", project_path=metadata["version_path"])

    assert result["status"] == "planned"
    assert result["pending_patch"] == {
        "op": "update_param",
        "node_selector": {"id": "3a4c97e"},
        "params": {"tripPoint": 3},
    }
    assert result["target_node"]["id"] == "3a4c97e"


def test_planner_can_target_unique_node_by_name_phrase(tmp_path: Path) -> None:
    metadata = create_project_version(AHU_TEMPLATE, project_id="planner_project", version_id="v_name", versions_dir=tmp_path)

    rename = plan_patch_request(
        "把名为 送风温度PID比例增益[P值] 的节点改名为 planner测试-P值",
        project_path=metadata["version_path"],
    )
    assert rename["status"] == "planned"
    assert rename["pending_patch"] == {
        "op": "rename_node",
        "node_selector": {"id": "795dbc3"},
        "new_name": "planner测试-P值",
    }

    update = plan_patch_request(
        "把名称为 送风温度PID比例增益[P值] 的禁止时输出改为 0.2",
        project_path=metadata["version_path"],
    )
    assert update["status"] == "planned"
    assert update["pending_patch"] == {
        "op": "update_param",
        "node_selector": {"id": "795dbc3"},
        "params": {"outOfServiceValue": 0.2},
    }


def test_planner_rejects_unknown_parameter_on_target_node(tmp_path: Path) -> None:
    metadata = create_project_version(AHU_TEMPLATE, project_id="planner_project", version_id="v_missing_param", versions_dir=tmp_path)

    result = plan_patch_request(
        "把名称为 送风温度PID比例增益[P值] 的阈值改为 3",
        project_path=metadata["version_path"],
    )

    assert result["status"] == "needs_clarification"
    assert result["pending_patch"] is None
    assert "不包含参数" in result["questions"][0]


def test_planner_requires_clarification_for_ambiguous_node(tmp_path: Path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="planner_project", version_id="v3", versions_dir=tmp_path)

    result = plan_patch_request("把比较判断改名为 不应自动执行", project_path=metadata["version_path"])

    assert result["status"] == "needs_clarification"
    assert result["pending_patch"] is None
    assert result["questions"]
    assert "不唯一" in result["reason"]
