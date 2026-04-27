from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class AgentState(TypedDict):
    messages: list[dict[str, Any]]
    project_type: str | None
    requirement_summary: dict[str, Any]
    template_candidates: list[dict[str, Any]]
    selected_template_id: str | None
    current_project_id: str | None
    current_project_version_id: str | None
    current_project_path: str | None
    pending_patch: dict[str, Any] | None
    planner_result: dict[str, Any] | None
    patch_result: dict[str, Any] | None
    validation_report: dict[str, Any] | None
    status: str
    next_action: str | None
    error: str | None
    auto_confirm_template: NotRequired[bool]
    versions_dir: NotRequired[str]
    project_created_in_current_run: NotRequired[bool]


def initial_state(message: str, *, project_type: str | None = None, auto_confirm_template: bool = False) -> AgentState:
    return {
        "messages": [{"role": "user", "content": message}],
        "project_type": project_type,
        "requirement_summary": {},
        "template_candidates": [],
        "selected_template_id": None,
        "current_project_id": None,
        "current_project_version_id": None,
        "current_project_path": None,
        "pending_patch": None,
        "planner_result": None,
        "patch_result": None,
        "validation_report": None,
        "status": "started",
        "next_action": None,
        "error": None,
        "auto_confirm_template": auto_confirm_template,
        "project_created_in_current_run": False,
    }
