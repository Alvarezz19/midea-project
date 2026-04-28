from __future__ import annotations

import uuid
from threading import Lock
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.checkpointing import get_checkpointer
from app.graph.nodes import (
    apply_pending_patch_node,
    classify_project_type,
    collect_requirements,
    create_project_version_node,
    plan_patch_node,
    retrieve_template_candidates,
    route_after_requirements,
    route_after_patch_planning,
    route_after_project_version,
    route_after_template_selection,
    select_or_wait_template,
    summarize_result_node,
    validate_current_project_node,
)
from app.graph.state import AgentState

_WORKFLOW: Any | None = None
_WORKFLOW_LOCK = Lock()


def build_workflow(*, checkpointer: Any | None = None):
    graph = StateGraph(AgentState)
    graph.add_node("classify_project_type", classify_project_type)
    graph.add_node("collect_requirements", collect_requirements)
    graph.add_node("retrieve_template_candidates", retrieve_template_candidates)
    graph.add_node("select_or_wait_template", select_or_wait_template)
    graph.add_node("create_project_version", create_project_version_node)
    graph.add_node("plan_patch", plan_patch_node)
    graph.add_node("apply_pending_patch", apply_pending_patch_node)
    graph.add_node("validate_current_project", validate_current_project_node)
    graph.add_node("summarize_result", summarize_result_node)

    graph.add_edge(START, "classify_project_type")
    graph.add_edge("classify_project_type", "collect_requirements")
    graph.add_conditional_edges(
        "collect_requirements",
        route_after_requirements,
        {
            "create_project_version": "create_project_version",
            "retrieve_template_candidates": "retrieve_template_candidates",
            "summarize_result": "summarize_result",
        },
    )
    graph.add_edge("retrieve_template_candidates", "select_or_wait_template")
    graph.add_conditional_edges(
        "select_or_wait_template",
        route_after_template_selection,
        {
            "create_project_version": "create_project_version",
            "summarize_result": "summarize_result",
        },
    )
    graph.add_conditional_edges(
        "create_project_version",
        route_after_project_version,
        {
            "apply_pending_patch": "apply_pending_patch",
            "plan_patch": "plan_patch",
            "validate_current_project": "validate_current_project",
        },
    )
    graph.add_conditional_edges(
        "plan_patch",
        route_after_patch_planning,
        {
            "apply_pending_patch": "apply_pending_patch",
            "summarize_result": "summarize_result",
        },
    )
    graph.add_edge("apply_pending_patch", "summarize_result")
    graph.add_edge("validate_current_project", "summarize_result")
    graph.add_edge("summarize_result", END)
    if checkpointer is None:
        return graph.compile()
    return graph.compile(checkpointer=checkpointer)


def get_workflow():
    """返回进程内复用的已编译工作流。"""

    global _WORKFLOW
    if _WORKFLOW is None:
        with _WORKFLOW_LOCK:
            if _WORKFLOW is None:
                _WORKFLOW = build_workflow(checkpointer=get_checkpointer())
    return _WORKFLOW


def invoke_workflow(state: AgentState, *, thread_id: str | None = None, workflow: Any | None = None) -> AgentState:
    compiled_workflow = workflow or get_workflow()
    if thread_id is None:
        thread_id = f"adhoc_{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    return compiled_workflow.invoke(state, config)
