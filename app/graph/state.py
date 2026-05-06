from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class AgentState(TypedDict):
    messages: list[dict[str, Any]]
    project_type: str | None
    requirement_summary: dict[str, Any]
    requirement_slots: dict[str, Any]
    design_brief: dict[str, Any] | None
    conformance_report: dict[str, Any] | None
    conversation_summary: dict[str, Any] | None
    recent_user_intents: list[dict[str, Any]]
    last_affected_node_ids: list[str]
    last_touched_entities: list[dict[str, Any]]
    last_patch_summary: dict[str, Any] | None
    semantic_target_candidates: list[dict[str, Any]]
    open_questions: list[dict[str, Any]]
    confirmed_requirements: list[str]
    template_candidates: list[dict[str, Any]]
    selected_template_id: str | None
    current_project_id: str | None
    current_project_version_id: str | None
    current_project_path: str | None
    pending_patch: dict[str, Any] | None
    pending_confirmation_patch: dict[str, Any] | None
    planner_result: dict[str, Any] | None
    planner_dry_run: dict[str, Any] | None
    planner_attempts: list[dict[str, Any]]
    risk_assessment: dict[str, Any] | None
    patch_confirmation: dict[str, Any] | None
    patch_result: dict[str, Any] | None
    validation_report: dict[str, Any] | None
    status: str
    next_action: str | None
    error: str | None
    auto_confirm_template: NotRequired[bool]
    versions_dir: NotRequired[str]
    project_created_in_current_run: NotRequired[bool]
    use_llm_planner: NotRequired[bool]
    llm_provider: NotRequired[str | None]
    llm_max_attempts: NotRequired[int]


def initial_state(message: str, *, project_type: str | None = None, auto_confirm_template: bool = False) -> AgentState:
    return {
        "messages": [{"role": "user", "content": message}],
        "project_type": project_type,
        "requirement_summary": {},
        "requirement_slots": {},
        "design_brief": None,
        "conformance_report": None,
        "conversation_summary": None,
        "recent_user_intents": [],
        "last_affected_node_ids": [],
        "last_touched_entities": [],
        "last_patch_summary": None,
        "semantic_target_candidates": [],
        "open_questions": [],
        "confirmed_requirements": [],
        "template_candidates": [],
        "selected_template_id": None,
        "current_project_id": None,
        "current_project_version_id": None,
        "current_project_path": None,
        "pending_patch": None,
        "pending_confirmation_patch": None,
        "planner_result": None,
        "planner_dry_run": None,
        "planner_attempts": [],
        "risk_assessment": None,
        "patch_confirmation": None,
        "patch_result": None,
        "validation_report": None,
        "status": "started",
        "next_action": None,
        "error": None,
        "auto_confirm_template": auto_confirm_template,
        "project_created_in_current_run": False,
        "use_llm_planner": False,
        "llm_provider": None,
        "llm_max_attempts": 2,
    }
