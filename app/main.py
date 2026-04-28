from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import configure_logging
from app.graph.state import AgentState
from app.graph.workflow import get_workflow, invoke_workflow
from app.services.json_project import get_project_version, list_project_versions, load_project, resolve_project_path
from app.services.knowledge import search_knowledge
from app.services.llm_planner import LLMPlannerError, plan_patch_with_llm
from app.services.patch_engine import PatchEngineError, dry_run_patch_to_project
from app.services.planner import plan_patch_request
from app.services.planner_execution import PlannerDryRunFeedbackError, plan_patch_with_llm_dry_run_feedback
from app.services.project_diff import ProjectDiffError, diff_project_versions
from app.services.requirement_extractor import RequirementExtractionError, extract_requirement_with_llm
from app.services.retrieval import RetrievalError, load_block_context, search_blocks, search_templates
from app.services.session_store import FileSessionStore
from app.services.validator import validate_project


configure_logging()

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.state.workflow = get_workflow()
app.state.session_store = FileSessionStore(settings.project_sessions_dir)
STATIC_DIR = Path(__file__).resolve().parent / "static"


class CreateSessionRequest(BaseModel):
    project_type: str | None = None
    auto_confirm_template: bool = False
    versions_dir: str | None = None
    use_llm_planner: bool = False
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


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/sessions")
def create_session(request: CreateSessionRequest | None = None) -> dict[str, Any]:
    request = request or CreateSessionRequest()
    thread_id = uuid.uuid4().hex
    state = _empty_state(
        project_type=request.project_type,
        auto_confirm_template=request.auto_confirm_template,
        versions_dir=request.versions_dir,
        use_llm_planner=request.use_llm_planner,
        llm_provider=request.llm_provider,
        llm_max_attempts=request.llm_max_attempts,
    )
    _session_store().save(thread_id, state)
    return {"thread_id": thread_id, "state": _public_state(state)}


@app.post("/api/sessions/{thread_id}/message")
def send_message(thread_id: str, request: MessageRequest) -> dict[str, Any]:
    state = _session_store().get(thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在。")

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
        next_state = invoke_workflow(state, thread_id=thread_id, workflow=app.state.workflow)
    except Exception as exc:  # API 边界兜底，内部节点仍应返回结构化错误。
        raise HTTPException(status_code=500, detail=f"工作流执行失败: {exc}") from exc

    _session_store().save(thread_id, next_state)
    return {"thread_id": thread_id, "state": _public_state(next_state)}


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
        _session_store().save(thread_id, state)
        return {"thread_id": thread_id, "state": _public_state(state)}

    state["pending_patch"] = pending_patch
    state["patch_confirmation"] = {
        "action": "approved",
        "confirmed_patch": pending_patch,
        "confirmed_project_version_id": state.get("current_project_version_id"),
        "confirmed_at": _utc_now_iso(),
        "risk_assessment": state.get("risk_assessment"),
    }
    try:
        next_state = invoke_workflow(state, thread_id=thread_id, workflow=app.state.workflow)
    except Exception as exc:  # API 边界兜底，内部节点仍应返回结构化错误。
        raise HTTPException(status_code=500, detail=f"工作流执行失败: {exc}") from exc

    _session_store().save(thread_id, next_state)
    return {"thread_id": thread_id, "state": _public_state(next_state)}


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
    if request.use_llm:
        try:
            return plan_patch_with_llm(
                request.message,
                project_path=str(path),
                template_id=request.template_id,
                project_type=request.project_type,
                provider=request.provider,
                max_attempts=request.llm_max_attempts,
            )
        except LLMPlannerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return plan_patch_request(
        request.message,
        project_path=str(path),
        template_id=request.template_id,
        project_type=request.project_type,
    )


@app.post("/api/planner/dry-run")
def planner_dry_run_api(request: PlannerDryRunRequest) -> dict[str, Any]:
    path = _resolve_allowed_project_file(request.project_path)
    if request.use_llm and request.pending_patch is None:
        return _planner_llm_dry_run_with_feedback(request, str(path))

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
        return {
            "status": "needs_clarification",
            "planner_result": planner_result,
            "pending_patch": None,
            "dry_run": None,
        }

    try:
        dry_run = dry_run_patch_to_project(str(path), pending_patch)
    except PatchEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dry_run.pop("nodes", None)
    return {
        "status": "dry_run_valid" if dry_run["valid"] else "dry_run_invalid",
        "planner_result": planner_result,
        "pending_patch": pending_patch,
        "dry_run": dry_run,
    }


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


@app.post("/api/projects/{project_id}/validate")
def validate_project_by_id(project_id: str) -> dict[str, Any]:
    path = _get_current_project_path(project_id)
    return validate_project(load_project(path))


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
    return validate_project(load_project(path))


@app.get("/api/projects/{project_id}/export")
def export_project_by_id(project_id: str) -> FileResponse:
    target = _get_current_project_path(project_id)
    return _export_valid_project_file(target)


@app.get("/api/projects/export")
def export_project(path: str) -> FileResponse:
    target = _resolve_allowed_project_file(path)
    return _export_valid_project_file(target)


def _export_valid_project_file(target: Path) -> FileResponse:
    report = validate_project(load_project(target))
    if not report["exportable"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "工程校验未通过，拒绝导出。",
                "validation_report": report,
            },
        )
    return FileResponse(
        target,
        media_type="application/json",
        filename=target.name,
    )


def _empty_state(
    *,
    project_type: str | None,
    auto_confirm_template: bool,
    versions_dir: str | None = None,
    use_llm_planner: bool = False,
    llm_provider: str | None = None,
    llm_max_attempts: int = 2,
) -> AgentState:
    state: AgentState = {
        "messages": [],
        "project_type": project_type,
        "requirement_summary": {},
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
    raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有可用工程版本。")


def _get_current_project_version_id(project_id: str) -> str:
    for state in _session_store().find_by_project_id(project_id):
        if state.get("current_project_id") == project_id and state.get("current_project_version_id"):
            return str(state["current_project_version_id"])
    raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有可用工程版本。")


def _get_versions_dir(project_id: str) -> str | None:
    for state in _session_store().find_by_project_id(project_id):
        versions_dir = state.get("versions_dir")
        if versions_dir:
            return str(versions_dir)
    return settings.project_versions_dir


def _session_store() -> FileSessionStore:
    return app.state.session_store


def _public_state(state: AgentState) -> dict[str, Any]:
    return {
        "messages": state.get("messages", []),
        "project_type": state.get("project_type"),
        "requirement_summary": state.get("requirement_summary", {}),
        "open_questions": state.get("open_questions", []),
        "confirmed_requirements": state.get("confirmed_requirements", []),
        "template_candidates": state.get("template_candidates", []),
        "selected_template_id": state.get("selected_template_id"),
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
        "status": state.get("status"),
        "next_action": state.get("next_action"),
        "error": state.get("error"),
        "use_llm_planner": state.get("use_llm_planner", False),
    }


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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
