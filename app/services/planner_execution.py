from __future__ import annotations

from typing import Any, Callable

from app.services.llm_planner import LLMPlannerError, plan_patch_with_llm
from app.services.patch_engine import PatchEngineError, dry_run_patch_to_project


class PlannerDryRunFeedbackError(ValueError):
    """LLM planner dry-run 多轮反馈后仍失败。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(str(payload.get("message", "LLM planner dry-run 重试后仍失败。")))


PlanPatchWithLLM = Callable[..., dict[str, Any]]
DryRunPatchToProject = Callable[[str, dict[str, Any]], dict[str, Any]]


def plan_patch_with_llm_dry_run_feedback(
    message: str,
    *,
    project_path: str,
    template_id: str | None = None,
    project_type: str | None = None,
    provider: str | None = None,
    llm_max_attempts: int = 2,
    conversation_context: dict[str, Any] | None = None,
    target_resolution: dict[str, Any] | None = None,
    current_project_context: dict[str, Any] | None = None,
    planner: PlanPatchWithLLM | None = None,
    dry_runner: DryRunPatchToProject | None = None,
) -> dict[str, Any]:
    """使用 LLM 规划补丁，并把 dry-run 失败反馈给模型重试。"""

    if llm_max_attempts < 1:
        raise LLMPlannerError("llm_max_attempts 必须大于 0。")

    planner = planner or plan_patch_with_llm
    dry_runner = dry_runner or dry_run_patch_to_project
    feedback_messages: list[str] = []
    planner_attempts: list[dict[str, Any]] = []
    last_error: str | None = None
    planner_result: dict[str, Any] | None = None
    pending_patch: dict[str, Any] | None = None

    for attempt_index in range(llm_max_attempts):
        planner_result = planner(
            message,
            project_path=project_path,
            template_id=template_id,
            project_type=project_type,
            provider=provider,
            max_attempts=llm_max_attempts,
            feedback_messages=feedback_messages,
            **_optional_context_kwargs(
                conversation_context=conversation_context,
                target_resolution=target_resolution,
                current_project_context=current_project_context,
            ),
        )
        pending_patch = _normalize_pending_patch(planner_result.get("pending_patch"))
        if pending_patch is not planner_result.get("pending_patch"):
            planner_result = {**planner_result, "pending_patch": pending_patch}
        planner_attempts.append(
            {
                "attempt": attempt_index + 1,
                "planner_status": planner_result.get("status"),
                "risk_level": planner_result.get("risk_level"),
                "operation_count": _operation_count(pending_patch),
                "llm_meta": planner_result.get("llm_meta") if isinstance(planner_result.get("llm_meta"), dict) else None,
            }
        )
        if not pending_patch:
            return {
                "status": "needs_clarification",
                "planner_result": planner_result,
                "pending_patch": None,
                "dry_run": None,
                "planner_attempts": planner_attempts,
            }

        try:
            dry_run = dry_runner(project_path, pending_patch)
        except PatchEngineError as exc:
            last_error = str(exc)
            feedback_messages.append(f"dry-run 执行失败：{last_error}")
            planner_attempts[-1]["dry_run_error"] = last_error
            continue

        dry_run.pop("nodes", None)
        if dry_run["valid"]:
            if dry_run.get("changed") is False:
                last_error = "补丁 dry-run 未产生任何实际变更。"
                feedback_messages.append(f"dry-run 未产生变更：{last_error}")
                planner_attempts[-1]["dry_run_valid"] = False
                planner_attempts[-1]["validation_error"] = last_error
                continue
            return {
                "status": "dry_run_valid",
                "planner_result": planner_result,
                "pending_patch": pending_patch,
                "dry_run": dry_run,
                "planner_attempts": planner_attempts,
            }

        last_error = summarize_validation_failure(dry_run.get("validation_report"))
        feedback_messages.append(f"dry-run 校验未通过：{last_error}")
        planner_attempts[-1]["dry_run_valid"] = False
        planner_attempts[-1]["validation_error"] = last_error

    raise PlannerDryRunFeedbackError(
        {
            "message": "LLM planner dry-run 重试后仍失败。",
            "last_error": last_error,
            "planner_attempts": planner_attempts,
            "planner_result": planner_result,
            "pending_patch": pending_patch,
        }
    )


def summarize_validation_failure(report: Any) -> str:
    if not isinstance(report, dict):
        return "缺少有效校验报告。"
    issues = report.get("issues")
    if not isinstance(issues, list) or not issues:
        return "校验报告标记为无效，但未返回具体问题。"
    summaries: list[str] = []
    for issue in issues[:5]:
        if not isinstance(issue, dict):
            continue
        code = issue.get("code", "unknown")
        message = issue.get("message", "")
        node_id = issue.get("node_id")
        summaries.append(f"{code}: {message} node_id={node_id}")
    return "；".join(summaries) if summaries else "校验报告中没有可读问题。"


def _operation_count(patch: Any) -> int:
    if not isinstance(patch, dict):
        return 0
    operations = patch.get("operations")
    if isinstance(operations, list):
        return len(operations)
    if patch.get("op"):
        return 1
    return 0


def _normalize_pending_patch(patch: Any) -> Any:
    if not isinstance(patch, dict):
        return patch
    operations = patch.get("operations")
    if isinstance(operations, list):
        return {
            **patch,
            "operations": [_normalize_operation(operation) for operation in operations],
        }
    return _normalize_operation(patch)


def _normalize_operation(operation: Any) -> Any:
    if not isinstance(operation, dict):
        return operation
    if operation.get("op") != "add_node_from_schema":
        return operation
    module_type = str(operation.get("module_type") or "")
    params = operation.get("params")
    if module_type != "constInput" or not isinstance(params, dict):
        return operation
    normalized_params = dict(params)
    if "user_defined_name" not in normalized_params:
        for alias in ("user_defined_name", "name", "label"):
            value = params.get(alias)
            if value not in (None, ""):
                normalized_params["user_defined_name"] = value
                break
    if "fixedValue" not in normalized_params:
        for alias in ("fixedValue", "value", "fixed_value"):
            value = params.get(alias)
            if value not in (None, ""):
                normalized_params["fixedValue"] = value
                break
    for alias in ("name", "label", "value", "fixed_value"):
        if alias in normalized_params and alias not in {"user_defined_name", "fixedValue"}:
            normalized_params.pop(alias, None)
    return {**operation, "params": normalized_params}


def _optional_context_kwargs(
    *,
    conversation_context: dict[str, Any] | None,
    target_resolution: dict[str, Any] | None,
    current_project_context: dict[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if conversation_context is not None:
        result["conversation_context"] = conversation_context
    if target_resolution is not None:
        result["target_resolution"] = target_resolution
    if current_project_context is not None:
        result["current_project_context"] = current_project_context
    return result
