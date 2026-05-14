from __future__ import annotations

from pathlib import Path

import pytest

from app.services.flow_graph import FlowGraphError, build_react_flow
from app.services.json_project import get_tabs, load_project


AHU_TEMPLATE = Path("programs/AHU程序/泰安宁阳中医院/flows_20260206160555.json")


def test_flow_graph_tab_filter_returns_only_selected_tab_nodes() -> None:
    nodes = load_project(AHU_TEMPLATE)
    tabs = get_tabs(nodes)
    timer_tab_id = _tab_id(tabs, "定时")
    outside_node_id = next(str(node["id"]) for node in nodes if node.get("type") != "tab" and node.get("z") != timer_tab_id)

    flow = build_react_flow(
        nodes,
        center_node_id=outside_node_id,
        focus_node_ids=[outside_node_id],
        tab_id=timer_tab_id,
        max_nodes=120,
        max_edges=260,
        max_chars=160000,
    )

    assert flow["budget"]["node_count"] == 4
    assert flow["budget"]["edge_count"] == 3
    assert flow["budget"]["truncated"] is False
    assert {node["data"]["tab_label"] for node in flow["nodes"]} == {"定时"}
    assert [node["data"]["label"] for node in flow["nodes"]] == ["TIME_EN", "变量", "逻辑运算", "TIME_CST"]


def test_flow_graph_tab_filter_handles_dx_fault_page_without_cross_tab_fill() -> None:
    nodes = load_project(AHU_TEMPLATE)
    tabs = get_tabs(nodes)
    fault_tab_id = _tab_id(tabs, "直膨机故障")

    flow = build_react_flow(nodes, tab_id=fault_tab_id, max_nodes=120, max_edges=260, max_chars=160000)

    assert flow["budget"]["node_count"] == 14
    assert flow["budget"]["truncated"] is False
    assert {node["data"]["tab_label"] for node in flow["nodes"]} == {"直膨机故障"}


def test_flow_graph_rejects_unknown_tab_id() -> None:
    nodes = load_project(AHU_TEMPLATE)

    with pytest.raises(FlowGraphError, match="页面不存在"):
        build_react_flow(nodes, tab_id="missing_tab")


def _tab_id(tabs: dict[str, str], label: str) -> str:
    return next(tab_id for tab_id, tab_label in tabs.items() if tab_label == label)
