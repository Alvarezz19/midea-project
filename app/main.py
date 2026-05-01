from __future__ import annotations

import tempfile
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import configure_logging
from app.graph.state import AgentState
from app.graph.workflow import (
    get_workflow,
    invoke_workflow_resume_with_updates,
    invoke_workflow_with_updates,
)
from app.services.flow_graph import FlowGraphError, build_react_flow
from app.services.json_project import (
    get_project_version,
    list_project_versions,
    load_project,
    resolve_project_path,
    update_project_version_metadata,
)
from app.services.knowledge import search_knowledge
from app.services.llm_planner import LLMPlannerError, plan_patch_with_llm
from app.services.observability import WorkflowEventInput, create_observability_store
from app.services.patch_engine import PatchEngineError, dry_run_patch_to_project
from app.services.planner import plan_patch_request
from app.services.planner_execution import PlannerDryRunFeedbackError, plan_patch_with_llm_dry_run_feedback
from app.services.project_diff import ProjectDiffError, diff_project_versions
from app.services.requirement_conformance import build_requirement_conformance_report, merge_conformance_into_validation
from app.services.requirement_extractor import RequirementExtractionError, extract_requirement_with_llm
from app.services.retrieval import RetrievalError, load_block_context, search_blocks, search_templates
from app.services.runtime_store import postgres_runtime_enabled
from app.services.session_store import create_session_store
from app.services.validator import validate_project


configure_logging()

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.state.workflow = get_workflow()
app.state.session_store = create_session_store(settings.project_sessions_dir)
app.state.observability_store = create_observability_store(Path(settings.project_sessions_dir).parent)
STATIC_DIR = Path(__file__).resolve().parent / "static"
WORKBENCH_DIR = STATIC_DIR / "workbench"
LEGACY_STATIC_DIR = STATIC_DIR / "legacy"


class CreateSessionRequest(BaseModel):
    project_type: str | None = None
    auto_confirm_template: bool = False
    versions_dir: str | None = None
    use_llm_planner: bool = True
    llm_provider: str | None = None
    llm_max_attempts: int = Field(default=2, ge=1, le=3)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1)
    project_type: str | None = None
    selected_template_id: str | None = None
    auto_confirm_template: bool | None = None
    pending_patch: dict[str, Any] | None = None
    use_llm_planner: bool | None = None
    llm_provider: str | None = None
    llm_max_attempts: int | None = Field(default=None, ge=1, le=3)


class TemplateSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    project_type: str | None = None
    limit: int = Field(default=3, ge=1, le=10)
    min_score: float = 0.0


class BlockSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    template_id: str | None = None
    project_type: str | None = None
    function_type: str | None = None
    limit: int = Field(default=8, ge=1, le=20)
    min_score: float = 0.0


class BlockContextRequest(BaseModel):
    block_id: str = Field(min_length=1)
    max_nodes: int = Field(default=80, ge=1, le=200)
    max_chars: int = Field(default=16000, ge=1000, le=100000)
    include_raw_nodes: bool = False


class ValidateProjectRequest(BaseModel):
    path: str


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    source_contains: str | None = None
    min_score: float = 0.1


class RequirementExtractRequest(BaseModel):
    message: str = Field(min_length=1)
    provider: str | None = None


class PlanPatchRequest(BaseModel):
    message: str = Field(min_length=1)
    project_path: str
    template_id: str | None = None
    project_type: str | None = None
    use_llm: bool = False
    provider: str | None = None
    llm_max_attempts: int = Field(default=2, ge=1, le=3)


class PlannerDryRunRequest(BaseModel):
    project_path: str
    message: str | None = None
    pending_patch: dict[str, Any] | None = None
    template_id: str | None = None
    project_type: str | None = None
    use_llm: bool = False
    provider: str | None = None
    llm_max_attempts: int = Field(default=2, ge=1, le=3)


class RollbackProjectRequest(BaseModel):
    target_version_id: str = Field(min_length=1)


class PatchConfirmationRequest(BaseModel):
    action: Literal["approve", "cancel"]


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1)
    project_id: str | None = None
    version_id: str | None = None
    rating: int = Field(ge=1, le=5)
    category: str = Field(min_length=1, max_length=60)
    comment: str = Field(default="", max_length=2000)


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    workbench_index = WORKBENCH_DIR / "index.html"
    if not workbench_index.exists():
        raise HTTPException(status_code=503, detail="React 工作台尚未构建，请先在 frontend 执行 npm run build。")
    return FileResponse(workbench_index)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/sessions/{thread_id}/events")
def stream_session_events(
    thread_id: str,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id")] = None,
    timeout_seconds: Annotated[float, Query(ge=0, le=30)] = 0,
) -> StreamingResponse:
    if _session_store().get(thread_id) is None and not _observability_store().list_events(thread_id, limit=1):
        raise HTTPException(status_code=404, detail="会话不存在。")

    last_event_id = last_event_id_header or last_event_id_query

    def event_stream():
        sent: set[str] = set()
        cursor = last_event_id
        deadline = time.monotonic() + timeout_seconds
        while True:
            events = _observability_store().list_events(thread_id, after_event_id=cursor, limit=1000)
            for event in events:
                event_id = str(event.get("event_id") or "")
                if event_id in sent:
                    continue
                sent.add(event_id)
                cursor = event_id
                yield _sse_encode(event)
            if timeout_seconds <= 0 or time.monotonic() >= deadline:
                break
            time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/sessions")
def create_session(request: CreateSessionRequest | None = None) -> dict[str, Any]:
    request = request or CreateSessionRequest()
    thread_id = uuid.uuid4().hex
    trace = _start_trace(thread_id, "create_session")
    state = _empty_state(
        project_type=request.project_type,
        auto_confirm_template=request.auto_confirm_template,
        versions_dir=request.versions_dir,
        use_llm_planner=request.use_llm_planner,
        llm_provider=request.llm_provider,
        llm_max_attempts=request.llm_max_attempts,
    )
    _session_store().save(thread_id, state)
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="workflow.session.created",
        step="create_session",
        status="completed",
        message="会话已创建",
        state=state,
    )
    _finish_trace(trace["trace_id"], "completed")
    return {"thread_id": thread_id, "trace_id": trace["trace_id"], "state": _public_state(state)}


@app.post("/api/sessions/{thread_id}/message")
def send_message(thread_id: str, request: MessageRequest) -> dict[str, Any]:
    state = _session_store().get(thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在。")
    trace = _start_trace(
        thread_id,
        request.message,
        project_id=state.get("current_project_id"),
        version_id=state.get("current_project_version_id"),
    )
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="workflow.message.received",
        step="receive_message",
        status="completed",
        message="已接收用户输入",
        state=state,
        payload={"has_pending_patch": request.pending_patch is not None},
    )

    if (
        request.selected_template_id is not None
        and state.get("status") == "awaiting_template_confirmation"
        and state.get("next_action") == "confirm_template"
        and _pending_interrupt_kind(thread_id) == "template_confirmation"
    ):
        try:
            next_state = invoke_workflow_resume_with_updates(
                {"action": "select_template", "selected_template_id": request.selected_template_id},
                thread_id=thread_id,
                workflow=app.state.workflow,
                base_state=state,
                on_update=_workflow_update_recorder(trace["trace_id"], thread_id),
            )
        except Exception as exc:  # API 边界兜底，内部节点仍应返回结构化错误。
            _record_event(
                trace["trace_id"],
                thread_id,
                event_type="workflow.resume.failed",
                step="template_confirmation",
                status="failed",
                message="模板确认恢复失败",
                state=state,
                payload={"error": str(exc)},
            )
            _finish_trace(trace["trace_id"], "failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"工作流恢复失败: {exc}") from exc
        _session_store().save(thread_id, _storable_state(next_state))
        _record_event(
            trace["trace_id"],
            thread_id,
            event_type="workflow.resume.completed",
            step="template_confirmation",
            status=str(next_state.get("status") or "completed"),
            message="模板确认已处理",
            state=next_state,
        )
        _finish_trace(trace["trace_id"], "completed")
        return {"thread_id": thread_id, "trace_id": trace["trace_id"], "state": _public_state(next_state)}

    state["messages"] = list(state.get("messages") or []) + [{"role": "user", "content": request.message}]
    if request.project_type is not None:
        state["project_type"] = request.project_type
    if request.selected_template_id is not None:
        state["selected_template_id"] = request.selected_template_id
    if request.auto_confirm_template is not None:
        state["auto_confirm_template"] = request.auto_confirm_template
    if request.pending_patch is not None:
        state["pending_patch"] = request.pending_patch
    if request.use_llm_planner is not None:
        state["use_llm_planner"] = request.use_llm_planner
    if request.llm_provider is not None:
        state["llm_provider"] = request.llm_provider
    if request.llm_max_attempts is not None:
        state["llm_max_attempts"] = request.llm_max_attempts

    try:
        next_state = invoke_workflow_with_updates(
            state,
            thread_id=thread_id,
            workflow=app.state.workflow,
            on_update=_workflow_update_recorder(trace["trace_id"], thread_id),
        )
    except Exception as exc:  # API 边界兜底，内部节点仍应返回结构化错误。
        _record_event(
            trace["trace_id"],
            thread_id,
            event_type="workflow.run.failed",
            step="invoke_workflow",
            status="failed",
            message="工作流执行失败",
            state=state,
            payload={"error": str(exc)},
        )
        _finish_trace(trace["trace_id"], "failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"工作流执行失败: {exc}") from exc

    _session_store().save(thread_id, _storable_state(next_state))
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="workflow.run.completed",
        step="invoke_workflow",
        status=str(next_state.get("status") or "completed"),
        message="工作流执行完成",
        state=next_state,
        payload={"next_action": next_state.get("next_action")},
    )
    _finish_trace(trace["trace_id"], "completed")
    return {"thread_id": thread_id, "trace_id": trace["trace_id"], "state": _public_state(next_state)}


@app.post("/api/sessions/{thread_id}/patch-confirmation")
def confirm_session_patch(thread_id: str, request: PatchConfirmationRequest) -> dict[str, Any]:
    state = _session_store().get(thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在。")
    if state.get("status") != "awaiting_patch_confirmation" or state.get("next_action") != "confirm_patch":
        raise HTTPException(status_code=400, detail="当前会话没有等待确认的补丁。")

    pending_patch = state.get("pending_confirmation_patch")
    if not isinstance(pending_patch, dict):
        raise HTTPException(status_code=400, detail="待确认补丁不存在或格式无效。")
    trace = _start_trace(
        thread_id,
        f"patch_confirmation:{request.action}",
        project_id=state.get("current_project_id"),
        version_id=state.get("current_project_version_id"),
    )
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="workflow.patch_confirmation.received",
        step="risk_confirmation",
        status="completed",
        message="已接收风险确认动作",
        state=state,
        payload={"action": request.action},
    )

    if _pending_interrupt_kind(thread_id) == "patch_confirmation":
        try:
            next_state = invoke_workflow_resume_with_updates(
                {"action": request.action},
                thread_id=thread_id,
                workflow=app.state.workflow,
                base_state=state,
                on_update=_workflow_update_recorder(trace["trace_id"], thread_id),
            )
        except Exception as exc:  # API 边界兜底，内部节点仍应返回结构化错误。
            _record_event(
                trace["trace_id"],
                thread_id,
                event_type="workflow.resume.failed",
                step="risk_confirmation",
                status="failed",
                message="补丁确认恢复失败",
                state=state,
                payload={"error": str(exc)},
            )
            _finish_trace(trace["trace_id"], "failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"工作流恢复失败: {exc}") from exc
        _session_store().save(thread_id, _storable_state(next_state))
        _record_event(
            trace["trace_id"],
            thread_id,
            event_type="workflow.patch_confirmation.completed",
            step="risk_confirmation",
            status=str(next_state.get("status") or "completed"),
            message="补丁确认已处理",
            state=next_state,
        )
        _finish_trace(trace["trace_id"], "completed")
        return {"thread_id": thread_id, "trace_id": trace["trace_id"], "state": _public_state(next_state)}

    if request.action == "cancel":
        state["pending_patch"] = None
        state["pending_confirmation_patch"] = None
        state["patch_confirmation"] = {
            "action": "cancelled",
            "cancelled_at": _utc_now_iso(),
            "risk_assessment": state.get("risk_assessment"),
        }
        state["status"] = "patch_confirmation_cancelled"
        state["next_action"] = "send_message"
        state["error"] = None
        state["messages"] = list(state.get("messages") or []) + [{"role": "assistant", "content": "已取消待确认补丁，当前工程版本未变化。"}]
        _session_store().save(thread_id, _storable_state(state))
        _record_event(
            trace["trace_id"],
            thread_id,
            event_type="workflow.patch_confirmation.cancelled",
            step="risk_confirmation",
            status="cancelled",
            message="用户取消高风险补丁",
            state=state,
        )
        _finish_trace(trace["trace_id"], "cancelled")
        return {"thread_id": thread_id, "trace_id": trace["trace_id"], "state": _public_state(state)}

    state["pending_patch"] = pending_patch
    state["patch_confirmation"] = {
        "action": "approved",
        "confirmed_patch": pending_patch,
        "confirmed_project_version_id": state.get("current_project_version_id"),
        "confirmed_at": _utc_now_iso(),
        "risk_assessment": state.get("risk_assessment"),
    }
    try:
        next_state = invoke_workflow_with_updates(
            state,
            thread_id=thread_id,
            workflow=app.state.workflow,
            on_update=_workflow_update_recorder(trace["trace_id"], thread_id),
        )
    except Exception as exc:  # API 边界兜底，内部节点仍应返回结构化错误。
        _record_event(
            trace["trace_id"],
            thread_id,
            event_type="workflow.run.failed",
            step="apply_confirmed_patch",
            status="failed",
            message="确认补丁应用失败",
            state=state,
            payload={"error": str(exc)},
        )
        _finish_trace(trace["trace_id"], "failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"工作流执行失败: {exc}") from exc

    _session_store().save(thread_id, _storable_state(next_state))
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="workflow.patch_confirmation.approved",
        step="apply_confirmed_patch",
        status=str(next_state.get("status") or "completed"),
        message="已应用确认补丁",
        state=next_state,
    )
    _finish_trace(trace["trace_id"], "completed")
    return {"thread_id": thread_id, "trace_id": trace["trace_id"], "state": _public_state(next_state)}


@app.post("/api/templates/search")
def search_templates_api(request: TemplateSearchRequest) -> dict[str, Any]:
    return {
        "items": search_templates(
            request.query,
            project_type=request.project_type,
            limit=request.limit,
            min_score=request.min_score,
        )
    }


@app.post("/api/blocks/search")
def search_blocks_api(request: BlockSearchRequest) -> dict[str, Any]:
    return {
        "items": search_blocks(
            request.query,
            template_id=request.template_id,
            project_type=request.project_type,
            function_type=request.function_type,
            limit=request.limit,
            min_score=request.min_score,
        )
    }


@app.post("/api/blocks/context")
def load_block_context_api(request: BlockContextRequest) -> dict[str, Any]:
    try:
        return load_block_context(
            request.block_id,
            max_nodes=request.max_nodes,
            max_chars=request.max_chars,
            include_raw_nodes=request.include_raw_nodes,
        )
    except RetrievalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/knowledge/search")
def search_knowledge_api(request: KnowledgeSearchRequest) -> dict[str, Any]:
    return {
        "items": search_knowledge(
            request.query,
            limit=request.limit,
            source_contains=request.source_contains,
            min_score=request.min_score,
        )
    }


@app.post("/api/requirements/extract")
def extract_requirement_api(request: RequirementExtractRequest) -> dict[str, Any]:
    try:
        return extract_requirement_with_llm(request.message, provider=request.provider)
    except RequirementExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/planner/plan")
def plan_patch_api(request: PlanPatchRequest) -> dict[str, Any]:
    path = _resolve_allowed_project_file(request.project_path)
    trace, thread_id, state = _api_trace_context(
        "planner_plan",
        project_path=path,
        project_type=request.project_type,
        template_id=request.template_id,
    )
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.planner.plan.started",
        step="plan_change",
        status="running",
        message="独立 planner 规划已开始",
        state=state,
        payload={"use_llm": request.use_llm, "template_id": request.template_id},
    )
    try:
        if request.use_llm:
            result = plan_patch_with_llm(
                request.message,
                project_path=str(path),
                template_id=request.template_id,
                project_type=request.project_type,
                provider=request.provider,
                max_attempts=request.llm_max_attempts,
            )
        else:
            result = plan_patch_request(
                request.message,
                project_path=str(path),
                template_id=request.template_id,
                project_type=request.project_type,
            )
    except LLMPlannerError as exc:
        _record_api_failure(trace["trace_id"], thread_id, state, "api.planner.plan.failed", "plan_change", "独立 planner 规划失败", exc)
        _finish_trace(trace["trace_id"], "failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.planner.plan.completed",
        step="plan_change",
        status=str(result.get("status") or "completed"),
        message="独立 planner 规划已完成",
        state=state,
        payload=_planner_result_summary(result),
    )
    _record_llm_call_from_result(trace["trace_id"], result, attempt=1)
    _finish_trace(trace["trace_id"], "completed")
    return {**result, "trace_id": trace["trace_id"]}


@app.post("/api/planner/dry-run")
def planner_dry_run_api(request: PlannerDryRunRequest) -> dict[str, Any]:
    path = _resolve_allowed_project_file(request.project_path)
    trace, thread_id, state = _api_trace_context(
        "planner_dry_run",
        project_path=path,
        project_type=request.project_type,
        template_id=request.template_id,
    )
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.planner.dry_run.started",
        step="dry_run",
        status="running",
        message="独立 planner dry-run 已开始",
        state=state,
        payload={"use_llm": request.use_llm, "has_pending_patch": request.pending_patch is not None},
    )
    try:
        if request.use_llm and request.pending_patch is None:
            result = _planner_llm_dry_run_with_feedback(request, str(path))
            _record_event(
                trace["trace_id"],
                thread_id,
                event_type="api.planner.dry_run.completed",
                step="dry_run",
                status=str(result.get("status") or "completed"),
                message="独立 LLM planner dry-run 已完成",
                state=state,
                payload=_planner_dry_run_result_summary(result),
            )
            _record_llm_calls_from_result(trace["trace_id"], result)
            _finish_trace(trace["trace_id"], "completed")
            return {**result, "trace_id": trace["trace_id"]}

        planner_result: dict[str, Any] | None = None
        pending_patch = request.pending_patch
        if pending_patch is None:
            if not request.message:
                raise HTTPException(status_code=400, detail="pending_patch 为空时必须提供 message。")
            planner_result = plan_patch_request(
                request.message,
                project_path=str(path),
                template_id=request.template_id,
                project_type=request.project_type,
            )
            pending_patch = planner_result.get("pending_patch")
        if not pending_patch:
            result = {
                "status": "needs_clarification",
                "planner_result": planner_result,
                "pending_patch": None,
                "dry_run": None,
            }
            _record_event(
                trace["trace_id"],
                thread_id,
                event_type="api.planner.dry_run.completed",
                step="dry_run",
                status="needs_clarification",
                message="独立 planner dry-run 需要澄清",
                state=state,
                payload=_planner_dry_run_result_summary(result),
            )
            _finish_trace(trace["trace_id"], "completed")
            return {**result, "trace_id": trace["trace_id"]}

        dry_run = dry_run_patch_to_project(str(path), pending_patch)
    except (LLMPlannerError, PlannerDryRunFeedbackError, PatchEngineError, HTTPException) as exc:
        _record_api_failure(trace["trace_id"], thread_id, state, "api.planner.dry_run.failed", "dry_run", "独立 planner dry-run 失败", exc)
        _finish_trace(trace["trace_id"], "failed", error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, PlannerDryRunFeedbackError):
            raise HTTPException(status_code=400, detail=exc.payload) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dry_run.pop("nodes", None)
    result = {
        "status": "dry_run_valid" if dry_run["valid"] else "dry_run_invalid",
        "planner_result": planner_result,
        "pending_patch": pending_patch,
        "dry_run": dry_run,
    }
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.planner.dry_run.completed",
        step="dry_run",
        status=str(result["status"]),
        message="独立 planner dry-run 已完成",
        state=state,
        payload=_planner_dry_run_result_summary(result),
    )
    _record_llm_calls_from_result(trace["trace_id"], result)
    _finish_trace(trace["trace_id"], "completed")
    return {**result, "trace_id": trace["trace_id"]}


def _planner_llm_dry_run_with_feedback(request: PlannerDryRunRequest, project_path: str) -> dict[str, Any]:
    if not request.message:
        raise HTTPException(status_code=400, detail="pending_patch 为空时必须提供 message。")

    try:
        return plan_patch_with_llm_dry_run_feedback(
            request.message,
            project_path=project_path,
            template_id=request.template_id,
            project_type=request.project_type,
            provider=request.provider,
            llm_max_attempts=request.llm_max_attempts,
        )
    except LLMPlannerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlannerDryRunFeedbackError as exc:
        raise HTTPException(status_code=400, detail=exc.payload) from exc


@app.get("/api/projects/{project_id}/versions")
def get_project_versions(project_id: str) -> dict[str, Any]:
    versions: list[dict[str, Any]] = []
    versions_dir = _get_versions_dir(project_id)
    if versions_dir is not None:
        versions = list_project_versions(project_id, versions_dir=versions_dir)
    if not versions:
        raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有该项目会话。")
    return {"project_id": project_id, "versions": versions}


@app.get("/api/projects/{project_id}/diff")
def get_project_diff(project_id: str, from_version_id: str | None = None, to_version_id: str | None = None) -> dict[str, Any]:
    versions_dir = _get_versions_dir(project_id)
    if versions_dir is None:
        raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有该项目会话。")

    if to_version_id is None:
        to_version_id = _get_current_project_version_id(project_id)
    try:
        to_metadata = get_project_version(project_id, to_version_id, versions_dir=versions_dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if from_version_id is None:
        parent_version_id = to_metadata.get("parent_version_id")
        if not isinstance(parent_version_id, str) or not parent_version_id:
            raise HTTPException(status_code=400, detail="from_version_id 为空且目标版本没有父版本，无法生成 diff。")
        from_version_id = parent_version_id

    try:
        return diff_project_versions(project_id, from_version_id, to_version_id, versions_dir=versions_dir)
    except ProjectDiffError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/versions/{version_id}/flow")
def get_project_version_flow(
    project_id: str,
    version_id: str,
    center_node_id: str | None = None,
    max_nodes: int = Query(default=80, ge=1, le=300),
    max_edges: int = Query(default=160, ge=0, le=800),
    max_chars: int = Query(default=120000, ge=1000, le=500000),
) -> dict[str, Any]:
    versions_dir = _get_versions_dir(project_id)
    if versions_dir is None:
        raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有该项目会话。")
    try:
        metadata = get_project_version(project_id, version_id, versions_dir=versions_dir)
        path = _resolve_allowed_project_file(str(metadata["version_path"]))
        flow = build_react_flow(
            load_project(path),
            center_node_id=center_node_id,
            max_nodes=max_nodes,
            max_edges=max_edges,
            max_chars=max_chars,
        )
    except (ValueError, FlowGraphError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "project_id": project_id,
        "version_id": version_id,
        "flow": flow,
    }


@app.post("/api/projects/{project_id}/validate")
def validate_project_by_id(project_id: str) -> dict[str, Any]:
    path = _get_current_project_path(project_id)
    version_id = _get_current_project_version_id(project_id)
    trace, thread_id, state = _api_trace_context("project_validate", project_id=project_id, version_id=version_id, project_path=path)
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.validation.started",
        step="validation",
        status="running",
        message="独立工程校验已开始",
        state=state,
    )
    try:
        nodes = load_project(path)
        report = _validate_with_requirement_gate(
            nodes,
            state=state,
            project_id=project_id,
            version_id=version_id,
            project_path=path,
        )
    except Exception as exc:
        _record_api_failure(trace["trace_id"], thread_id, state, "api.validation.failed", "validation", "独立工程校验失败", exc)
        _finish_trace(trace["trace_id"], "failed", error=str(exc))
        raise
    _record_validation_if_needed(project_id, version_id, report)
    _persist_conformance_context(
        thread_id=thread_id,
        state=state,
        report=report,
        project_id=project_id,
        version_id=version_id,
    )
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.validation.completed",
        step="validation",
        status="completed" if report.get("valid") else "failed",
        message="独立工程校验已完成",
        state=state,
        payload=_validation_summary_payload(report),
    )
    _finish_trace(trace["trace_id"], "completed" if report.get("valid") else "failed")
    return {**report, "trace_id": trace["trace_id"]}


@app.post("/api/projects/{project_id}/rollback")
def rollback_project(project_id: str, request: RollbackProjectRequest) -> dict[str, Any]:
    session_items = _session_store().find_items_by_project_id(project_id)
    if not session_items:
        raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有该项目会话。")

    versions_dir = _get_versions_dir(project_id)
    try:
        metadata = get_project_version(project_id, request.target_version_id, versions_dir=versions_dir)
        target_path = _resolve_allowed_project_file(str(metadata["version_path"]))
        report = validate_project(load_project(target_path))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not report["valid"]:
        raise HTTPException(status_code=400, detail="目标版本校验未通过，拒绝回滚。")
    if postgres_runtime_enabled():
        from app.services.postgres_runtime import set_current_project_version

        metadata = set_current_project_version(project_id, request.target_version_id, validation_report=report)

    public_state: dict[str, Any] | None = None
    for thread_id, state in session_items:
        state["current_project_version_id"] = str(metadata["version_id"])
        state["current_project_path"] = str(metadata["version_path"])
        state["validation_report"] = report
        state["status"] = "rolled_back"
        state["next_action"] = None
        state["error"] = None
        _session_store().save(thread_id, state)
        if public_state is None:
            public_state = _public_state(state)

    return {
        "project_id": project_id,
        "target_version_id": request.target_version_id,
        "state": public_state,
        "validation_report": report,
    }


@app.post("/api/projects/validate")
def validate_project_api(request: ValidateProjectRequest) -> dict[str, Any]:
    path = _resolve_allowed_project_file(request.path)
    trace, thread_id, state = _api_trace_context("project_validate_by_path", project_path=path)
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.validation.started",
        step="validation",
        status="running",
        message="独立工程校验已开始",
        state=state,
    )
    try:
        nodes = load_project(path)
        report = _validate_with_requirement_gate(nodes, state=state, project_path=path)
    except Exception as exc:
        _record_api_failure(trace["trace_id"], thread_id, state, "api.validation.failed", "validation", "独立工程校验失败", exc)
        _finish_trace(trace["trace_id"], "failed", error=str(exc))
        raise
    _record_event(
        trace["trace_id"],
        thread_id,
        event_type="api.validation.completed",
        step="validation",
        status="completed" if report.get("valid") else "failed",
        message="独立工程校验已完成",
        state=state,
        payload=_validation_summary_payload(report),
    )
    _finish_trace(trace["trace_id"], "completed" if report.get("valid") else "failed")
    return {**report, "trace_id": trace["trace_id"]}


@app.get("/api/projects/{project_id}/export")
def export_project_by_id(project_id: str) -> FileResponse:
    target = _get_current_project_path(project_id)
    version_id = _get_current_project_version_id(project_id)
    trace, thread_id, state = _api_trace_context("project_export", project_id=project_id, version_id=version_id, project_path=target)
    return _export_valid_project_file(target, trace_id=trace["trace_id"], thread_id=thread_id, state=state)


@app.get("/api/projects/export")
def export_project(path: str) -> FileResponse:
    target = _resolve_allowed_project_file(path)
    trace, thread_id, state = _api_trace_context("project_export_by_path", project_path=target)
    return _export_valid_project_file(target, trace_id=trace["trace_id"], thread_id=thread_id, state=state)


@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: str) -> dict[str, Any]:
    trace = _observability_store().get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace 不存在。")
    return trace


@app.get("/api/traces/{trace_id}/feedback")
def get_trace_feedback(trace_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    if _observability_store().get_trace(trace_id) is None:
        raise HTTPException(status_code=404, detail="trace 不存在。")
    return {"trace_id": trace_id, "feedback": _observability_store().list_feedback(trace_id=trace_id, limit=limit)}


@app.get("/api/projects/{project_id}/traces")
def get_project_traces(project_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"project_id": project_id, "traces": _observability_store().list_project_traces(project_id, limit=limit)}


@app.get("/api/projects/{project_id}/feedback")
def get_project_feedback(project_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"project_id": project_id, "feedback": _observability_store().list_feedback(project_id=project_id, limit=limit)}


@app.get("/api/observability/costs")
def get_observability_costs(
    project_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return _observability_store().cost_summary(project_id=project_id, limit=limit)


@app.get("/api/observability/trends")
def get_observability_trends(
    project_id: str | None = None,
    limit: int = Query(default=30, ge=1, le=120),
) -> dict[str, Any]:
    return _observability_store().trend_summary(project_id=project_id, limit=limit)


@app.post("/api/feedback")
def submit_feedback(request: FeedbackRequest) -> dict[str, Any]:
    if _observability_store().get_trace(request.trace_id) is None:
        raise HTTPException(status_code=404, detail="trace 不存在，无法提交反馈。")
    return _observability_store().record_feedback(
        trace_id=request.trace_id,
        project_id=request.project_id,
        version_id=request.version_id,
        rating=request.rating,
        category=request.category,
        comment=request.comment,
    )


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(_observability_store().metrics_text(), media_type="text/plain; version=0.0.4")


def _export_valid_project_file(target: Path, *, trace_id: str, thread_id: str, state: dict[str, Any]) -> FileResponse:
    _record_event(
        trace_id,
        thread_id,
        event_type="api.export.started",
        step="export",
        status="running",
        message="工程导出检查已开始",
        state=state,
    )
    try:
        nodes = load_project(target)
        report = _validate_with_requirement_gate(
            nodes,
            state=state,
            project_id=state.get("current_project_id"),
            version_id=state.get("current_project_version_id"),
            project_path=target,
        )
    except Exception as exc:
        _record_api_failure(trace_id, thread_id, state, "api.export.failed", "export", "工程导出检查失败", exc)
        _finish_trace(trace_id, "failed", error=str(exc))
        raise
    _record_event(
        trace_id,
        thread_id,
        event_type="api.validation.completed",
        step="validation",
        status="completed" if report.get("valid") else "failed",
        message="导出前校验已完成",
        state=state,
        payload=_validation_summary_payload(report),
    )
    if not report["exportable"]:
        _record_event(
            trace_id,
            thread_id,
            event_type="api.export.failed",
            step="export",
            status="failed",
            message="工程校验未通过，拒绝导出",
            state=state,
            payload=_validation_summary_payload(report),
        )
        _finish_trace(trace_id, "failed", error="工程校验未通过，拒绝导出。")
        raise HTTPException(
            status_code=400,
            detail={
                "message": "工程校验未通过，拒绝导出。",
                "validation_report": report,
                "trace_id": trace_id,
            },
        )
    _persist_conformance_context(
        thread_id=thread_id,
        state=state,
        report=report,
        project_id=state.get("current_project_id"),
        version_id=state.get("current_project_version_id"),
    )
    _record_export_if_needed(str(target), report)
    _record_event(
        trace_id,
        thread_id,
        event_type="api.export.completed",
        step="export",
        status="completed",
        message="工程导出已通过校验",
        state=state,
        payload=_validation_summary_payload(report),
    )
    _finish_trace(trace_id, "completed")
    return FileResponse(
        target,
        media_type="application/json",
        filename=target.name,
        headers={"X-Trace-Id": trace_id},
    )


def _validate_with_requirement_gate(
    nodes: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    project_id: Any | None = None,
    version_id: Any | None = None,
    project_path: str | Path | None = None,
) -> dict[str, Any]:
    report = validate_project(nodes)
    context = _requirement_context_for_project(
        state=state,
        project_id=str(project_id) if project_id else None,
        version_id=str(version_id) if version_id else None,
        project_path=project_path,
    )
    conformance = build_requirement_conformance_report(
        nodes=nodes,
        requirement_slots=context.get("requirement_slots") if isinstance(context.get("requirement_slots"), dict) else None,
        design_brief=context.get("design_brief") if isinstance(context.get("design_brief"), dict) else None,
    )
    return merge_conformance_into_validation(report, conformance)


def _requirement_context_for_project(
    *,
    state: dict[str, Any],
    project_id: str | None = None,
    version_id: str | None = None,
    project_path: str | Path | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if isinstance(state.get("requirement_slots"), dict) and state.get("requirement_slots"):
        context["requirement_slots"] = state["requirement_slots"]
    if isinstance(state.get("design_brief"), dict) and state.get("design_brief"):
        context["design_brief"] = state["design_brief"]
    if context:
        return context

    metadata = _version_metadata_for_context(project_id=project_id, version_id=version_id, project_path=project_path)
    requirement_context = metadata.get("requirement_context") if isinstance(metadata, dict) else None
    if isinstance(requirement_context, dict):
        return requirement_context
    return {}


def _version_metadata_for_context(
    *,
    project_id: str | None,
    version_id: str | None,
    project_path: str | Path | None,
) -> dict[str, Any]:
    if project_id and version_id:
        try:
            return get_project_version(project_id, version_id, versions_dir=_get_versions_dir(project_id))
        except ValueError:
            return {}
    if project_path is None:
        return {}
    meta_path = _resolve_allowed_project_file(project_path).with_suffix(".meta.json")
    if not meta_path.exists():
        return {}
    try:
        with meta_path.open("r", encoding="utf-8") as file:
            metadata = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _persist_conformance_context(
    *,
    thread_id: str,
    state: dict[str, Any],
    report: dict[str, Any],
    project_id: Any | None,
    version_id: Any | None,
) -> None:
    conformance = report.get("conformance_report")
    if isinstance(conformance, dict) and thread_id and not thread_id.startswith("api_"):
        stored = _session_store().get(thread_id)
        if stored is not None:
            stored["validation_report"] = report
            stored["conformance_report"] = conformance
            _session_store().save(thread_id, stored)
    if not project_id or not version_id or not isinstance(conformance, dict):
        return
    requirement_context = _requirement_context_for_project(
        state=state,
        project_id=str(project_id),
        version_id=str(version_id),
    )
    if requirement_context:
        requirement_context = {**requirement_context, "conformance_report": conformance}
    else:
        requirement_context = {"conformance_report": conformance}
    try:
        update_project_version_metadata(
            str(project_id),
            str(version_id),
            {
                "exportable": bool(report.get("exportable")),
                "validation_summary": _validation_summary(report),
                "requirement_context": requirement_context,
            },
            versions_dir=_get_versions_dir(str(project_id)) or settings.project_versions_dir,
        )
    except ValueError:
        return


def _empty_state(
    *,
    project_type: str | None,
    auto_confirm_template: bool,
    versions_dir: str | None = None,
    use_llm_planner: bool = True,
    llm_provider: str | None = None,
    llm_max_attempts: int = 2,
) -> AgentState:
    state: AgentState = {
        "messages": [],
        "project_type": project_type,
        "requirement_summary": {},
        "requirement_slots": {},
        "design_brief": None,
        "conformance_report": None,
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
        "status": "created",
        "next_action": "send_message",
        "error": None,
        "auto_confirm_template": auto_confirm_template,
        "project_created_in_current_run": False,
        "use_llm_planner": use_llm_planner,
        "llm_provider": llm_provider,
        "llm_max_attempts": llm_max_attempts,
    }
    state["versions_dir"] = versions_dir or settings.project_versions_dir
    return state


def _get_current_project_path(project_id: str) -> Path:
    for state in _session_store().find_by_project_id(project_id):
        if state.get("current_project_id") == project_id and state.get("current_project_path"):
            return _resolve_allowed_project_file(str(state["current_project_path"]))
    if postgres_runtime_enabled():
        from app.services.postgres_runtime import current_project_version

        metadata = current_project_version(project_id)
        if metadata and metadata.get("version_path"):
            return _resolve_allowed_project_file(str(metadata["version_path"]))
    raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有可用工程版本。")


def _get_current_project_version_id(project_id: str) -> str:
    for state in _session_store().find_by_project_id(project_id):
        if state.get("current_project_id") == project_id and state.get("current_project_version_id"):
            return str(state["current_project_version_id"])
    if postgres_runtime_enabled():
        from app.services.postgres_runtime import current_project_version

        metadata = current_project_version(project_id)
        if metadata and metadata.get("version_id"):
            return str(metadata["version_id"])
    raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有可用工程版本。")


def _get_versions_dir(project_id: str) -> str | None:
    for state in _session_store().find_by_project_id(project_id):
        versions_dir = state.get("versions_dir")
        if versions_dir:
            return str(versions_dir)
    return settings.project_versions_dir


def _session_store():
    return app.state.session_store


def _observability_store():
    session_store = _session_store()
    desired_root = Path(getattr(session_store, "root_dir", Path(settings.project_sessions_dir))).parent
    store = getattr(app.state, "observability_store", None)
    if not postgres_runtime_enabled() and (store is None or Path(getattr(store, "root_dir", desired_root)) != desired_root):
        store = create_observability_store(desired_root)
        app.state.observability_store = store
    return store


def _start_trace(
    thread_id: str,
    root_input: str,
    *,
    project_id: Any | None = None,
    version_id: Any | None = None,
) -> dict[str, Any]:
    return _observability_store().create_trace(
        thread_id=thread_id,
        root_input=root_input,
        project_id=str(project_id) if project_id else None,
        version_id=str(version_id) if version_id else None,
    )


def _api_trace_context(
    root_input: str,
    *,
    project_id: str | None = None,
    version_id: str | None = None,
    project_path: str | Path | None = None,
    project_type: str | None = None,
    template_id: str | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    thread_id, state = _find_session_context(project_id=project_id, project_path=project_path)
    state = dict(state or {})
    if project_id and not state.get("current_project_id"):
        state["current_project_id"] = project_id
    if version_id and not state.get("current_project_version_id"):
        state["current_project_version_id"] = version_id
    if project_path and not state.get("current_project_path"):
        state["current_project_path"] = str(project_path)
    if project_type and not state.get("project_type"):
        state["project_type"] = project_type
    if template_id and not state.get("selected_template_id"):
        state["selected_template_id"] = template_id
    trace = _start_trace(
        thread_id,
        root_input,
        project_id=state.get("current_project_id"),
        version_id=state.get("current_project_version_id"),
    )
    return trace, thread_id, state


def _find_session_context(
    *,
    project_id: str | None = None,
    project_path: str | Path | None = None,
) -> tuple[str, dict[str, Any] | None]:
    if project_id:
        items = _session_store().find_items_by_project_id(project_id)
        if items:
            return items[0]
    if project_path is not None:
        target = resolve_project_path(project_path)
        for thread_id, state in _session_store().list_items():
            current_path = state.get("current_project_path")
            if current_path and resolve_project_path(str(current_path)) == target:
                return thread_id, state
    return f"api_{uuid.uuid4().hex}", None


def _finish_trace(trace_id: str, status: str, *, error: str | None = None) -> None:
    _observability_store().finish_trace(trace_id, status=status, error=error)


def _workflow_update_recorder(trace_id: str, thread_id: str):
    def record(step: str, update: Any, state: AgentState) -> None:
        event_step = _event_step_name(step, update)
        payload = _workflow_update_payload(step, update)
        status = _event_status_from_update(step, update)
        _record_event(
            trace_id,
            thread_id,
            event_type="workflow.interrupt.created" if step == "__interrupt__" else "workflow.step.completed",
            step=event_step,
            status=status,
            message=_workflow_step_message(step, update),
            state=state,
            payload=payload,
        )
        for derived in _derived_workflow_events(step, update):
            _record_event(
                trace_id,
                thread_id,
                event_type=derived["event_type"],
                step=derived["step"],
                status=derived["status"],
                message=derived["message"],
                state=state,
                payload=derived["payload"],
            )
        _record_llm_calls_from_update(trace_id, update)

    return record


def _event_step_name(step: str, update: Any) -> str:
    if step != "__interrupt__":
        return _WORKFLOW_STEP_NAMES.get(step, step)
    kind = _interrupt_kind(update)
    if kind == "template_confirmation":
        return "template_confirmation"
    if kind == "patch_confirmation":
        return "risk_confirmation"
    return "interrupt"


def _event_status_from_update(step: str, update: Any) -> str:
    if step == "__interrupt__":
        return "waiting"
    if isinstance(update, dict):
        status = update.get("status")
        if isinstance(status, str) and status:
            return status
        if update.get("error"):
            return "failed"
    return "completed"


def _workflow_step_message(step: str, update: Any) -> str:
    if step == "__interrupt__":
        kind = _interrupt_kind(update)
        if kind == "template_confirmation":
            return "等待用户确认模板"
        if kind == "patch_confirmation":
            return "等待用户确认中高风险补丁"
        return "工作流进入人工确认点"
    return _WORKFLOW_STEP_MESSAGES.get(step, f"工作流步骤完成：{step}")


def _workflow_update_payload(step: str, update: Any) -> dict[str, Any]:
    if step == "__interrupt__":
        return {"node": step, "interrupts": _interrupt_payload(update)}
    if not isinstance(update, dict):
        return {"node": step, "value": str(update)}

    payload: dict[str, Any] = {
        "node": step,
        "updated_fields": sorted(str(key) for key in update.keys()),
    }
    if update.get("status") is not None:
        payload["status"] = update.get("status")
    if update.get("next_action") is not None:
        payload["next_action"] = update.get("next_action")
    if update.get("error") is not None:
        payload["error"] = str(update.get("error"))
    if isinstance(update.get("template_candidates"), list):
        payload["template_candidate_count"] = len(update["template_candidates"])
    if isinstance(update.get("planner_result"), dict):
        planner_result = update["planner_result"]
        payload["planner"] = {
            "status": planner_result.get("status"),
            "planner": planner_result.get("planner"),
            "risk_level": planner_result.get("risk_level"),
            "operation_count": _operation_count(planner_result.get("pending_patch") or update.get("pending_patch")),
        }
    if isinstance(update.get("planner_attempts"), list):
        payload["planner_attempt_count"] = len(update["planner_attempts"])
    if isinstance(update.get("planner_dry_run"), dict):
        payload["dry_run"] = _dry_run_summary(update["planner_dry_run"])
    if isinstance(update.get("patch_result"), dict):
        payload["patch_result"] = _patch_result_summary(update["patch_result"])
    if isinstance(update.get("risk_assessment"), dict):
        risk = update["risk_assessment"]
        payload["risk_assessment"] = {
            "risk_level": risk.get("risk_level"),
            "requires_confirmation": risk.get("requires_confirmation"),
            "reason_count": len(risk.get("reasons") or []),
        }
    if isinstance(update.get("validation_report"), dict):
        payload["validation_summary"] = _validation_summary_payload(update["validation_report"])
    if update.get("current_project_id") or update.get("current_project_version_id"):
        payload["project"] = {
            "project_id": update.get("current_project_id"),
            "version_id": update.get("current_project_version_id"),
            "project_created_in_current_run": update.get("project_created_in_current_run"),
        }
    return payload


def _derived_workflow_events(step: str, update: Any) -> list[dict[str, Any]]:
    if not isinstance(update, dict):
        return []
    events: list[dict[str, Any]] = []

    if step == "plan_patch" and isinstance(update.get("planner_attempts"), list):
        for item in _planner_attempt_events(update["planner_attempts"]):
            events.append(item)
    if step == "plan_patch" and isinstance(update.get("planner_dry_run"), dict):
        dry_run = _dry_run_summary(update["planner_dry_run"])
        events.append(
            {
                "event_type": "workflow.dry_run.completed",
                "step": "dry_run",
                "status": "completed" if dry_run.get("valid") else "failed",
                "message": "补丁 dry-run 已完成",
                "payload": dry_run,
            }
        )
    if step == "apply_pending_patch" and isinstance(update.get("patch_result"), dict):
        events.append(
            {
                "event_type": "workflow.patch.applied",
                "step": "submit_version",
                "status": _event_status_from_update(step, update),
                "message": "结构化补丁已应用并生成版本",
                "payload": _patch_result_summary(update["patch_result"]),
            }
        )
    if step in {"apply_pending_patch", "validate_current_project"} and isinstance(update.get("validation_report"), dict):
        summary = _validation_summary_payload(update["validation_report"])
        events.append(
            {
                "event_type": "workflow.validation.completed",
                "step": "validation",
                "status": "completed" if summary["valid"] else "failed",
                "message": "工程校验已完成",
                "payload": summary,
            }
        )
    if step == "create_project_version" and update.get("current_project_version_id"):
        events.append(
            {
                "event_type": "workflow.version.created",
                "step": "submit_version",
                "status": "completed",
                "message": "工程版本已创建",
                "payload": {
                    "project_id": update.get("current_project_id"),
                    "version_id": update.get("current_project_version_id"),
                    "project_created_in_current_run": update.get("project_created_in_current_run"),
                },
            }
        )
    return events


def _planner_attempt_events(attempts: list[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            continue
        error = attempt.get("error") or attempt.get("last_error")
        meta = attempt.get("llm_meta") if isinstance(attempt.get("llm_meta"), dict) else {}
        events.append(
            {
                "event_type": "llm.call.failed" if error else "llm.call.completed",
                "step": "llm_planner",
                "status": "failed" if error else "completed",
                "message": "LLM planner 调用失败" if error else "LLM planner 调用完成",
                "payload": {
                    "attempt": attempt.get("attempt") or index,
                    "provider": attempt.get("provider") or meta.get("provider"),
                    "model": attempt.get("model") or meta.get("model"),
                    "prompt_name": attempt.get("prompt_name") or meta.get("prompt_name"),
                    "latency_ms": meta.get("latency_ms"),
                    "usage": _llm_usage_summary(meta.get("usage")),
                    "status": attempt.get("status"),
                    "risk_level": attempt.get("risk_level"),
                    "operation_count": _operation_count(attempt.get("pending_patch")),
                    "error": str(error) if error else None,
                },
            }
        )
    return events


def _record_llm_calls_from_update(trace_id: str, update: Any) -> None:
    if not isinstance(update, dict):
        return
    if isinstance(update.get("planner_attempts"), list):
        for index, attempt in enumerate(update["planner_attempts"], start=1):
            if not isinstance(attempt, dict):
                continue
            _record_llm_call_from_meta(
                trace_id,
                attempt.get("llm_meta") if isinstance(attempt.get("llm_meta"), dict) else None,
                attempt=int(attempt.get("attempt") or index),
                status="failed" if attempt.get("error") or attempt.get("last_error") else "completed",
                error=str(attempt.get("error") or attempt.get("last_error")) if attempt.get("error") or attempt.get("last_error") else None,
            )
        return
    if isinstance(update.get("planner_result"), dict):
        _record_llm_call_from_result(trace_id, update["planner_result"], attempt=1)


def _record_llm_calls_from_result(trace_id: str, result: dict[str, Any]) -> None:
    attempts = result.get("planner_attempts")
    if isinstance(attempts, list) and attempts:
        for index, attempt in enumerate(attempts, start=1):
            if not isinstance(attempt, dict):
                continue
            _record_llm_call_from_meta(
                trace_id,
                attempt.get("llm_meta") if isinstance(attempt.get("llm_meta"), dict) else None,
                attempt=int(attempt.get("attempt") or index),
                status="failed" if attempt.get("error") or attempt.get("last_error") else "completed",
                error=str(attempt.get("error") or attempt.get("last_error")) if attempt.get("error") or attempt.get("last_error") else None,
            )
        return
    planner_result = result.get("planner_result") if isinstance(result.get("planner_result"), dict) else result
    if isinstance(planner_result, dict):
        _record_llm_call_from_result(trace_id, planner_result, attempt=1)


def _record_llm_call_from_result(trace_id: str, result: dict[str, Any], *, attempt: int) -> None:
    _record_llm_call_from_meta(
        trace_id,
        result.get("llm_meta") if isinstance(result.get("llm_meta"), dict) else None,
        attempt=attempt,
        status="completed",
        error=None,
    )


def _record_llm_call_from_meta(
    trace_id: str,
    meta: dict[str, Any] | None,
    *,
    attempt: int,
    status: str,
    error: str | None,
) -> None:
    if not meta and status == "completed":
        return
    usage = meta.get("usage") if isinstance(meta, dict) and isinstance(meta.get("usage"), dict) else {}
    _observability_store().record_llm_call(
        trace_id=trace_id,
        provider=str(meta.get("provider") if isinstance(meta, dict) else "unknown"),
        model=str(meta.get("model") if isinstance(meta, dict) else "unknown"),
        prompt_name=str(meta.get("prompt_name") if isinstance(meta, dict) else "llm_planner"),
        attempt=attempt,
        latency_ms=int(round(float(meta.get("latency_ms") or 0))) if isinstance(meta, dict) else 0,
        input_tokens=_usage_int(usage, "prompt_tokens"),
        output_tokens=_usage_int(usage, "completion_tokens"),
        estimated_cost=0,
        status=status,
        error=error,
    )


def _llm_usage_summary(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {
        "input_tokens": _usage_int(value, "prompt_tokens"),
        "output_tokens": _usage_int(value, "completion_tokens"),
        "total_tokens": _usage_int(value, "total_tokens"),
    }


def _usage_int(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    return int(raw) if isinstance(raw, int | float) and raw >= 0 else 0


def _record_api_failure(
    trace_id: str,
    thread_id: str,
    state: dict[str, Any],
    event_type: str,
    step: str,
    message: str,
    exc: Exception,
) -> None:
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    _record_event(
        trace_id,
        thread_id,
        event_type=event_type,
        step=step,
        status="failed",
        message=message,
        state=state,
        payload={"error": _error_payload(detail)},
    )


def _error_payload(detail: Any) -> Any:
    if isinstance(detail, dict):
        return {key: detail.get(key) for key in ("message", "trace_id") if key in detail} or "结构化错误"
    return str(detail)


def _planner_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "planner": result.get("planner"),
        "risk_level": result.get("risk_level"),
        "requires_confirmation": result.get("requires_confirmation"),
        "operation_count": _operation_count(result.get("pending_patch")),
        "planner_attempt_count": result.get("planner_attempt_count"),
    }


def _planner_dry_run_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "status": result.get("status"),
        "planner_result": _planner_result_summary(result["planner_result"]) if isinstance(result.get("planner_result"), dict) else None,
        "operation_count": _operation_count(result.get("pending_patch")),
    }
    if isinstance(result.get("dry_run"), dict):
        payload["dry_run"] = _dry_run_summary(result["dry_run"])
    if isinstance(result.get("planner_attempts"), list):
        payload["planner_attempt_count"] = len(result["planner_attempts"])
    return payload


def _interrupt_kind(value: Any) -> str | None:
    for item in value if isinstance(value, list) else [value]:
        interrupt_value = getattr(item, "value", item)
        if isinstance(interrupt_value, dict) and isinstance(interrupt_value.get("kind"), str):
            return str(interrupt_value["kind"])
    return None


def _interrupt_payload(value: Any) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else [value]:
        interrupt_value = getattr(item, "value", item)
        interrupt_id = getattr(item, "id", None)
        summary: dict[str, Any] = {"id": interrupt_id}
        if isinstance(interrupt_value, dict):
            summary["kind"] = interrupt_value.get("kind")
            summary["question"] = interrupt_value.get("question")
            if isinstance(interrupt_value.get("template_candidates"), list):
                summary["template_candidate_count"] = len(interrupt_value["template_candidates"])
            if isinstance(interrupt_value.get("risk_assessment"), dict):
                risk = interrupt_value["risk_assessment"]
                summary["risk_assessment"] = {
                    "risk_level": risk.get("risk_level"),
                    "requires_confirmation": risk.get("requires_confirmation"),
                    "reason_count": len(risk.get("reasons") or []),
                }
        else:
            summary["value"] = str(interrupt_value)
        payload.append(summary)
    return payload


def _dry_run_summary(value: dict[str, Any]) -> dict[str, Any]:
    report = value.get("validation_report") if isinstance(value.get("validation_report"), dict) else {}
    diff = value.get("diff") if isinstance(value.get("diff"), dict) else {}
    return {
        "valid": value.get("valid"),
        "changed": value.get("changed"),
        "change_count": len(value.get("changes") or []),
        "diff_summary": diff.get("summary"),
        "validation_summary": _validation_summary_payload(report),
    }


def _patch_result_summary(value: dict[str, Any]) -> dict[str, Any]:
    diff = value.get("diff") if isinstance(value.get("diff"), dict) else {}
    return {
        "changed": value.get("changed"),
        "change_count": len(value.get("changes") or []),
        "diff_summary": diff.get("summary"),
    }


def _validation_summary_payload(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    conformance = report.get("conformance_report") if isinstance(report.get("conformance_report"), dict) else {}
    return {
        "valid": bool(report.get("valid")),
        "exportable": bool(report.get("exportable")),
        "error_count": int(report.get("error_count") or summary.get("error_count") or 0),
        "warning_count": int(report.get("warning_count") or summary.get("warning_count") or 0),
        "risk_count": int(report.get("risk_count") or summary.get("risk_count") or 0),
        "blocked_export_reasons": report.get("blocked_export_reasons", []),
        "requirement_reviewed": conformance.get("context_available"),
        "requirement_valid": conformance.get("valid_for_requirement"),
    }


def _operation_count(patch: Any) -> int:
    if not isinstance(patch, dict):
        return 0
    operations = patch.get("operations")
    if isinstance(operations, list):
        return len(operations)
    if patch.get("op"):
        return 1
    return 0


_WORKFLOW_STEP_NAMES = {
    "classify_project_type": "requirement_analysis",
    "collect_requirements": "requirement_analysis",
    "retrieve_template_candidates": "template_retrieval",
    "select_or_wait_template": "template_confirmation",
    "confirm_template_interrupt": "template_confirmation",
    "create_project_version": "submit_version",
    "plan_patch": "plan_change",
    "apply_pending_patch": "submit_version",
    "confirm_patch_interrupt": "risk_confirmation",
    "validate_current_project": "validation",
    "summarize_result": "summarize_result",
}


_WORKFLOW_STEP_MESSAGES = {
    "classify_project_type": "项目类型识别已完成",
    "collect_requirements": "结构化需求分析已完成",
    "retrieve_template_candidates": "模板检索已完成",
    "select_or_wait_template": "模板选择状态已更新",
    "confirm_template_interrupt": "模板确认已处理",
    "create_project_version": "工程版本创建步骤已完成",
    "plan_patch": "结构化修改计划已完成",
    "apply_pending_patch": "补丁执行步骤已完成",
    "confirm_patch_interrupt": "风险确认已处理",
    "validate_current_project": "工程校验步骤已完成",
    "summarize_result": "工作流摘要已生成",
}


def _record_event(
    trace_id: str,
    thread_id: str,
    *,
    event_type: str,
    step: str,
    status: str,
    message: str,
    state: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _observability_store().record_event(
        WorkflowEventInput(
            trace_id=trace_id,
            thread_id=thread_id,
            project_id=str(state.get("current_project_id")) if state.get("current_project_id") else None,
            version_id=str(state.get("current_project_version_id")) if state.get("current_project_version_id") else None,
            event_type=event_type,
            step=step,
            status=status,
            message=message,
            payload=payload or {},
        )
    )


def _sse_encode(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "")
    event_type = str(event.get("event_type") or "message")
    data = json_dumps(event)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _validation_summary(report: dict[str, Any]) -> dict[str, Any]:
    conformance = report.get("conformance_report") if isinstance(report.get("conformance_report"), dict) else {}
    conformance_summary = conformance.get("summary") if isinstance(conformance.get("summary"), dict) else {}
    return {
        "valid": report.get("valid"),
        "exportable": report.get("exportable"),
        "error_count": report.get("error_count"),
        "warning_count": report.get("warning_count"),
        "risk_count": (report.get("summary") or {}).get("risk_count") if isinstance(report.get("summary"), dict) else None,
        "blocked_export_reasons": report.get("blocked_export_reasons", []),
        "requirement_reviewed": conformance.get("context_available"),
        "requirement_valid": conformance.get("valid_for_requirement"),
        "conformance_blocked_count": conformance_summary.get("blocked_count"),
        "conformance_missing_count": conformance_summary.get("missing_count"),
    }


def _record_validation_if_needed(project_id: str, version_id: str | None, report: dict[str, Any]) -> None:
    if not postgres_runtime_enabled():
        return
    from app.services.postgres_runtime import record_project_validation

    record_project_validation(project_id, version_id, report)


def _record_export_if_needed(project_path: str, report: dict[str, Any]) -> None:
    if not postgres_runtime_enabled():
        return
    from app.services.postgres_runtime import record_project_export

    for state in _session_store().list_states():
        if state.get("current_project_path") == project_path:
            project_id = state.get("current_project_id")
            if project_id:
                record_project_export(str(project_id), state.get("current_project_version_id"), report)
            return


def _public_state(state: AgentState) -> dict[str, Any]:
    validation_report = state.get("validation_report")
    return {
        "messages": state.get("messages", []),
        "project_type": state.get("project_type"),
        "requirement_summary": state.get("requirement_summary", {}),
        "requirement_slots": state.get("requirement_slots", {}),
        "design_brief": state.get("design_brief"),
        "conformance_report": state.get("conformance_report"),
        "open_questions": state.get("open_questions", []),
        "confirmed_requirements": state.get("confirmed_requirements", []),
        "template_candidates": state.get("template_candidates", []),
        "selected_template_id": state.get("selected_template_id"),
        "project_id": state.get("current_project_id"),
        "version_id": state.get("current_project_version_id"),
        "current_project_id": state.get("current_project_id"),
        "current_project_version_id": state.get("current_project_version_id"),
        "current_project_path": state.get("current_project_path"),
        "patch_result": state.get("patch_result"),
        "pending_confirmation_patch": state.get("pending_confirmation_patch"),
        "planner_result": state.get("planner_result"),
        "planner_dry_run": state.get("planner_dry_run"),
        "planner_attempts": state.get("planner_attempts", []),
        "risk_assessment": state.get("risk_assessment"),
        "patch_confirmation": state.get("patch_confirmation"),
        "validation_report": state.get("validation_report"),
        "validation_summary": _validation_summary(validation_report) if isinstance(validation_report, dict) else None,
        "status": state.get("status"),
        "next_action": state.get("next_action"),
        "error": state.get("error"),
        "use_llm_planner": state.get("use_llm_planner", True),
        "interrupts": _public_interrupts(state.get("__interrupt__")),
    }


def _storable_state(state: AgentState) -> AgentState:
    data = dict(state)
    data.pop("__interrupt__", None)
    return data  # type: ignore[return-value]


def _pending_interrupt_kind(thread_id: str) -> str | None:
    try:
        snapshot = app.state.workflow.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return None
    for item in getattr(snapshot, "interrupts", ()) or ():
        value = getattr(item, "value", None)
        if isinstance(value, dict) and isinstance(value.get("kind"), str):
            return str(value["kind"])
    return None


def _public_interrupts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        interrupt_value = getattr(item, "value", None)
        interrupt_id = getattr(item, "id", None)
        result.append(
            {
                "id": interrupt_id,
                "value": interrupt_value,
            }
        )
    return result


def _resolve_allowed_project_file(path: str) -> Path:
    target = resolve_project_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="工程文件不存在。")
    if target.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="只允许访问 JSON 文件。")
    # 当前阶段允许导出临时目录中的验收文件和项目内文件；生产环境应替换为项目权限校验。
    if not target.is_relative_to(resolve_project_path(".")) and not str(target).startswith(str(Path(tempfile.gettempdir()).resolve())):
        raise HTTPException(status_code=403, detail="文件路径不在允许范围内。")
    return target


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/legacy", StaticFiles(directory=LEGACY_STATIC_DIR, html=True), name="legacy")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
