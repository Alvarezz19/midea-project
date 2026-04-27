from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.graph.state import AgentState
from app.graph.workflow import invoke_workflow
from app.services.json_project import load_project, resolve_project_path
from app.services.knowledge import search_knowledge
from app.services.planner import plan_patch_request
from app.services.retrieval import search_templates
from app.services.validator import validate_project


app = FastAPI(title="Midea JSON Agent Prototype", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"

SESSIONS: dict[str, AgentState] = {}


class CreateSessionRequest(BaseModel):
    project_type: str | None = None
    auto_confirm_template: bool = False
    versions_dir: str | None = None


class MessageRequest(BaseModel):
    message: str = Field(min_length=1)
    project_type: str | None = None
    selected_template_id: str | None = None
    auto_confirm_template: bool | None = None
    pending_patch: dict[str, Any] | None = None


class TemplateSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    project_type: str | None = None
    limit: int = Field(default=3, ge=1, le=10)
    min_score: float = 0.0


class ValidateProjectRequest(BaseModel):
    path: str


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    source_contains: str | None = None
    min_score: float = 0.1


class PlanPatchRequest(BaseModel):
    message: str = Field(min_length=1)
    project_path: str
    template_id: str | None = None
    project_type: str | None = None


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
    SESSIONS[thread_id] = _empty_state(
        project_type=request.project_type,
        auto_confirm_template=request.auto_confirm_template,
        versions_dir=request.versions_dir,
    )
    return {"thread_id": thread_id, "state": _public_state(SESSIONS[thread_id])}


@app.post("/api/sessions/{thread_id}/message")
def send_message(thread_id: str, request: MessageRequest) -> dict[str, Any]:
    state = SESSIONS.get(thread_id)
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

    try:
        next_state = invoke_workflow(state)
    except Exception as exc:  # API 边界兜底，内部节点仍应返回结构化错误。
        raise HTTPException(status_code=500, detail=f"工作流执行失败: {exc}") from exc

    SESSIONS[thread_id] = next_state
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


@app.post("/api/planner/plan")
def plan_patch_api(request: PlanPatchRequest) -> dict[str, Any]:
    _resolve_allowed_project_file(request.project_path)
    return plan_patch_request(
        request.message,
        project_path=request.project_path,
        template_id=request.template_id,
        project_type=request.project_type,
    )


@app.get("/api/projects/{project_id}/versions")
def get_project_versions(project_id: str) -> dict[str, Any]:
    versions = []
    for state in SESSIONS.values():
        if state.get("current_project_id") == project_id:
            versions.append(
                {
                    "version_id": state.get("current_project_version_id"),
                    "path": state.get("current_project_path"),
                    "status": state.get("status"),
                }
            )
    if not versions:
        raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有该项目会话。")
    return {"project_id": project_id, "versions": versions}


@app.post("/api/projects/{project_id}/validate")
def validate_project_by_id(project_id: str) -> dict[str, Any]:
    path = _get_current_project_path(project_id)
    return validate_project(load_project(path))


@app.post("/api/projects/validate")
def validate_project_api(request: ValidateProjectRequest) -> dict[str, Any]:
    path = _resolve_allowed_project_file(request.path)
    return validate_project(load_project(path))


@app.get("/api/projects/{project_id}/export")
def export_project_by_id(project_id: str) -> FileResponse:
    target = _get_current_project_path(project_id)
    return FileResponse(
        target,
        media_type="application/json",
        filename=target.name,
    )


@app.get("/api/projects/export")
def export_project(path: str) -> FileResponse:
    target = _resolve_allowed_project_file(path)
    return FileResponse(
        target,
        media_type="application/json",
        filename=target.name,
    )


def _empty_state(*, project_type: str | None, auto_confirm_template: bool, versions_dir: str | None = None) -> AgentState:
    state: AgentState = {
        "messages": [],
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
        "status": "created",
        "next_action": "send_message",
        "error": None,
        "auto_confirm_template": auto_confirm_template,
        "project_created_in_current_run": False,
    }
    if versions_dir is not None:
        state["versions_dir"] = versions_dir
    return state


def _get_current_project_path(project_id: str) -> Path:
    for state in SESSIONS.values():
        if state.get("current_project_id") == project_id and state.get("current_project_path"):
            return _resolve_allowed_project_file(str(state["current_project_path"]))
    raise HTTPException(status_code=404, detail="项目不存在或当前进程中没有可用工程版本。")


def _public_state(state: AgentState) -> dict[str, Any]:
    return {
        "messages": state.get("messages", []),
        "project_type": state.get("project_type"),
        "requirement_summary": state.get("requirement_summary", {}),
        "template_candidates": state.get("template_candidates", []),
        "selected_template_id": state.get("selected_template_id"),
        "current_project_id": state.get("current_project_id"),
        "current_project_version_id": state.get("current_project_version_id"),
        "current_project_path": state.get("current_project_path"),
        "patch_result": state.get("patch_result"),
        "planner_result": state.get("planner_result"),
        "validation_report": state.get("validation_report"),
        "status": state.get("status"),
        "next_action": state.get("next_action"),
        "error": state.get("error"),
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
