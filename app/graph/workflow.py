from __future__ import annotations

import uuid
from threading import Lock
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph.checkpointing import get_checkpointer
from app.graph.nodes import (
    apply_pending_patch_node,
    classify_project_type,
    confirm_patch_interrupt_node,
    confirm_template_interrupt_node,
    collect_requirements,
    create_project_version_node,
    plan_patch_node,
    route_after_patch_application,
    route_after_patch_confirmation,
    retrieve_template_candidates,
    route_after_requirements,
    route_after_patch_planning,
    route_after_project_version,
    route_after_template_confirmation,
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
    graph.add_node("confirm_template_interrupt", confirm_template_interrupt_node)
    graph.add_node("create_project_version", create_project_version_node)
    graph.add_node("plan_patch", plan_patch_node)
    graph.add_node("apply_pending_patch", apply_pending_patch_node)
    graph.add_node("confirm_patch_interrupt", confirm_patch_interrupt_node)
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
            "confirm_template_interrupt": "confirm_template_interrupt",
            "summarize_result": "summarize_result",
        },
    )
    graph.add_conditional_edges(
        "confirm_template_interrupt",
        route_after_template_confirmation,
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
    graph.add_conditional_edges(
        "apply_pending_patch",
        route_after_patch_application,
        {
            "confirm_patch_interrupt": "confirm_patch_interrupt",
            "summarize_result": "summarize_result",
        },
    )
    graph.add_conditional_edges(
        "confirm_patch_interrupt",
        route_after_patch_confirmation,
        {
            "apply_pending_patch": "apply_pending_patch",
            "summarize_result": "summarize_result",
        },
    )
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


def invoke_workflow_resume(resume: Any, *, thread_id: str, workflow: Any | None = None) -> AgentState:
    """从 LangGraph interrupt 检查点恢复执行。"""

    if not thread_id:
        raise ValueError("thread_id 不能为空。")
    compiled_workflow = workflow or get_workflow()
    config = {"configurable": {"thread_id": thread_id}}
    return compiled_workflow.invoke(Command(resume=resume), config)


WorkflowUpdateCallback = Callable[[str, Any, AgentState], None]


def invoke_workflow_with_updates(
    state: AgentState,
    *,
    thread_id: str | None = None,
    workflow: Any | None = None,
    on_update: WorkflowUpdateCallback | None = None,
) -> AgentState:
    """执行工作流并逐节点暴露 LangGraph updates。"""

    if thread_id is None:
        thread_id = f"adhoc_{uuid.uuid4().hex}"
    return _stream_workflow_to_state(state, base_state=state, thread_id=thread_id, workflow=workflow, on_update=on_update)


def invoke_workflow_resume_with_updates(
    resume: Any,
    *,
    thread_id: str,
    workflow: Any | None = None,
    base_state: AgentState | None = None,
    on_update: WorkflowUpdateCallback | None = None,
) -> AgentState:
    """从 interrupt 恢复执行，并逐节点暴露 LangGraph updates。"""

    if not thread_id:
        raise ValueError("thread_id 不能为空。")
    return _stream_workflow_to_state(
        Command(resume=resume),
        base_state=base_state or {},
        thread_id=thread_id,
        workflow=workflow,
        on_update=on_update,
    )


def _stream_workflow_to_state(
    workflow_input: Any,
    *,
    base_state: dict[str, Any],
    thread_id: str,
    workflow: Any | None,
    on_update: WorkflowUpdateCallback | None,
) -> AgentState:
    compiled_workflow = workflow or get_workflow()
    config = {"configurable": {"thread_id": thread_id}}
    accumulated: dict[str, Any] = dict(base_state)

    for chunk in compiled_workflow.stream(workflow_input, config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for step, update in chunk.items():
            if isinstance(update, dict):
                accumulated.update(update)
            else:
                accumulated[step] = update
            if on_update is not None:
                on_update(str(step), update, accumulated)  # type: ignore[arg-type]

    return accumulated  # type: ignore[return-value]
