from __future__ import annotations

from pathlib import Path

from app.services.semantic_locator import locate_semantic_targets, public_semantic_candidates


PLANT_TEMPLATE = Path("programs/机房群控程序/风冷热泵标准控制程序[风冷涡旋]20240905.json")


def test_semantic_locator_resolves_tab_and_node_type() -> None:
    result = locate_semantic_targets(
        "把水泵控制里的比较判断改名为 演示节点",
        project_path=PLANT_TEMPLATE,
        project_type="plant_room",
        conversation_context={},
    )

    assert result["status"] == "resolved"
    assert result["intent"] == "rename_node"
    assert result["selected"]["selector"] == {"id": "3a4c97e"}
    assert result["selected"]["tab_label"] == "水泵控制"
    assert result["selected"]["type"] == "compare"
    assert "页面" in result["selected"]["reason"]


def test_semantic_locator_resolves_recent_node_reference() -> None:
    result = locate_semantic_targets(
        "把刚才那个节点的阈值改为 3",
        project_path=PLANT_TEMPLATE,
        project_type="plant_room",
        conversation_context={"last_affected_node_ids": ["3a4c97e"]},
    )

    assert result["status"] == "resolved"
    assert result["intent"] == "update_param"
    assert result["selected"]["selector"] == {"id": "3a4c97e"}
    assert result["selected"]["confidence"] >= 0.9
    assert "最近目标引用" in result["selected"]["reason"]


def test_semantic_locator_returns_user_readable_candidates() -> None:
    result = locate_semantic_targets(
        "把比较判断改名为 演示节点",
        project_path=PLANT_TEMPLATE,
        project_type="plant_room",
        conversation_context={},
    )

    assert result["status"] == "candidates"
    assert result["intent"] == "rename_node"
    assert 1 <= len(result["candidates"]) <= 5
    assert "请确认要修改哪一个" in result["questions"][0]

    public_candidates = public_semantic_candidates(result)
    assert public_candidates
    assert all(candidate["display_name"] for candidate in public_candidates)
    assert all("node_id" not in candidate for candidate in public_candidates)
    assert all("selector" in candidate for candidate in public_candidates)
