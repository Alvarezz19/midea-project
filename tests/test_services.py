from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.json_project import create_project_version, find_nodes, load_project, save_project, summarize_project
from app.services.knowledge import get_knowledge_context, load_knowledge_chunks, search_knowledge
from app.services.patch_engine import PatchEngineError, apply_patch
from app.services.planner import plan_patch_request
from app.services.retrieval import (
    RetrievalError,
    get_template_by_id,
    load_node_neighborhood,
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
    assert validate_project(load_project(version_path))["valid"]


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


def test_retrieval_reports_missing_node() -> None:
    with pytest.raises(RetrievalError, match="锚点节点不存在"):
        load_node_neighborhood(PLANT_TEMPLATE, ["missing-node"], depth=1)


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


def test_patch_engine_rejects_unsafe_or_ambiguous_changes() -> None:
    nodes = load_project(PLANT_TEMPLATE)

    with pytest.raises(PatchEngineError, match="必须唯一"):
        apply_patch(nodes, {"op": "rename_node", "node_selector": {"type": "compare"}, "new_name": "不应执行"})

    with pytest.raises(PatchEngineError, match="不允许修改结构字段"):
        apply_patch(nodes, {"op": "update_param", "node_selector": {"id": "67febfa"}, "params": {"id": "bad"}})


def test_index_files_are_parseable() -> None:
    template_index = json.loads(Path("indexes/templates/template_index.json").read_text(encoding="utf-8"))
    assert len(template_index) == 5

    tab_lines = Path("indexes/tabs/tab_index.jsonl").read_text(encoding="utf-8").splitlines()
    node_lines = Path("indexes/nodes/node_index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(tab_lines) == 31
    assert len(node_lines) == 8413
    assert all(isinstance(json.loads(line), dict) for line in tab_lines[:5])
    assert all(isinstance(json.loads(line), dict) for line in node_lines[:5])


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


def test_planner_requires_clarification_for_ambiguous_node(tmp_path: Path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="planner_project", version_id="v3", versions_dir=tmp_path)

    result = plan_patch_request("把比较判断改名为 不应自动执行", project_path=metadata["version_path"])

    assert result["status"] == "needs_clarification"
    assert result["pending_patch"] is None
    assert result["questions"]
    assert "不唯一" in result["reason"]
