from __future__ import annotations

from pathlib import Path

from app.services.json_project import create_project_version, create_project_version_from_nodes, find_nodes, load_project
from app.services.project_semantic_index import (
    build_project_semantic_index,
    search_current_project_nodes,
    summarize_affected_nodes,
    summarize_current_project_for_planner,
)


PLANT_TEMPLATE = Path("programs/机房群控程序/风冷热泵标准控制程序[风冷涡旋]20240905.json")


def test_current_project_semantic_index_finds_pump_compare_node() -> None:
    index = build_project_semantic_index(PLANT_TEMPLATE)

    assert index["summary"]["node_count"] == 3450
    assert any(tab["label"] == "水泵控制" for tab in index["tabs"])
    assert "compare" in index["by_type"]

    results = search_current_project_nodes("水泵控制 比较判断", project_path=PLANT_TEMPLATE, limit=3)

    assert results
    assert results[0]["node_id"] == "3a4c97e"
    assert results[0]["tab_label"] == "水泵控制"
    assert results[0]["schema_path"] == "schemas/logic/比较判断.json"
    assert "tripPoint" in results[0]["key_params"]
    assert results[0]["selector"] == {"id": "3a4c97e"}


def test_current_project_semantic_search_uses_child_version_after_rename(tmp_path: Path) -> None:
    parent = create_project_version(
        PLANT_TEMPLATE,
        project_id="semantic_project",
        version_id="v_parent",
        versions_dir=tmp_path,
    )
    child_nodes = load_project(parent["version_path"])
    find_nodes(child_nodes, {"id": "3a4c97e"})[0]["name"] = "演示节点"
    child = create_project_version_from_nodes(
        child_nodes,
        project_id="semantic_project",
        parent_version_id="v_parent",
        version_id="v_child",
        versions_dir=tmp_path,
        validation_report={"valid": True, "error_count": 0, "warning_count": 0},
    )

    old_results = search_current_project_nodes("演示节点", project_path=parent["version_path"], limit=3)
    new_results = search_current_project_nodes("演示节点", project_path=child["version_path"], limit=3)

    assert old_results == []
    assert new_results
    assert new_results[0]["node_id"] == "3a4c97e"
    assert new_results[0]["display_name"].startswith("水泵控制 / 演示节点")


def test_current_project_summary_and_affected_nodes_are_budgeted() -> None:
    summary = summarize_current_project_for_planner(PLANT_TEMPLATE, max_nodes=5, max_chars=4000)

    assert summary["budget"]["returned_nodes"] <= 5
    assert summary["budget"]["truncated"] is True
    assert summary["tabs"]

    affected = summarize_affected_nodes(PLANT_TEMPLATE, ["3a4c97e"])

    assert len(affected) == 1
    assert affected[0]["kind"] == "node"
    assert affected[0]["id"] == "3a4c97e"
    assert affected[0]["selector"] == {"id": "3a4c97e"}
    assert affected[0]["display_name"] == "水泵控制 / 比较判断 / 比较判断 (Compare)"
    assert affected[0]["tab_id"] == "73b96a8"
    assert affected[0]["tab_label"] == "水泵控制"
    assert affected[0]["type"] == "compare"
    assert affected[0]["name"] == "比较判断"
    assert "tripPoint" in affected[0]["key_params"]
