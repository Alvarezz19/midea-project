from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.json_project import ROOT_DIR, resolve_project_path
from app.services.runtime_store import postgres_runtime_enabled


class ObservabilityStoreError(ValueError):
    """可观测性数据读写失败。"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex}"


def new_event_id() -> str:
    timestamp = int(time.time() * 1000)
    return f"evt_{timestamp}_{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class WorkflowEventInput:
    trace_id: str
    thread_id: str
    event_type: str
    step: str
    status: str
    message: str = ""
    project_id: str | None = None
    version_id: str | None = None
    payload: dict[str, Any] | None = None


class FileObservabilityStore:
    """文件型可观测性存储，用于本地开发和无 PostgreSQL 环境。"""

    def __init__(self, root_dir: str | Path = ROOT_DIR / "projects") -> None:
        self.root_dir = resolve_project_path(root_dir)
        self.events_dir = self.root_dir / "events"
        self.traces_dir = self.root_dir / "traces"
        self.feedback_path = self.root_dir / "feedback" / "user_feedback.jsonl"

    def create_trace(
        self,
        *,
        thread_id: str,
        root_input: str,
        project_id: str | None = None,
        version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace = {
            "trace_id": new_trace_id(),
            "thread_id": thread_id,
            "project_id": project_id,
            "version_id": version_id,
            "root_input": root_input,
            "status": "running",
            "started_at": utc_now_iso(),
            "finished_at": None,
            "error": None,
            "metadata": metadata or {},
            "events": [],
        }
        self._write_trace(trace)
        return trace

    def finish_trace(self, trace_id: str, *, status: str, error: str | None = None) -> dict[str, Any] | None:
        trace = self.get_trace(trace_id)
        if trace is None:
            return None
        trace["status"] = status
        trace["finished_at"] = utc_now_iso()
        trace["error"] = error
        self._write_trace(trace)
        return trace

    def record_event(self, event: WorkflowEventInput) -> dict[str, Any]:
        item = {
            "event_id": new_event_id(),
            "trace_id": event.trace_id,
            "thread_id": event.thread_id,
            "project_id": event.project_id,
            "version_id": event.version_id,
            "event_type": event.event_type,
            "step": event.step,
            "status": event.status,
            "message": event.message,
            "payload": event.payload or {},
            "created_at": utc_now_iso(),
        }
        self._append_jsonl(self._events_path(event.thread_id), item)
        trace = self.get_trace(event.trace_id)
        if trace is not None:
            events = trace.get("events")
            if isinstance(events, list):
                events.append(item)
            trace["project_id"] = event.project_id or trace.get("project_id")
            trace["version_id"] = event.version_id or trace.get("version_id")
            self._write_trace(trace)
        return item

    def list_events(self, thread_id: str, *, after_event_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        items = self._read_jsonl(self._events_path(thread_id))
        if after_event_id:
            index = next((idx for idx, item in enumerate(items) if item.get("event_id") == after_event_id), None)
            items = items[index + 1 :] if index is not None else items
        return items[:limit]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        path = self._trace_path(trace_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (json.JSONDecodeError, OSError) as exc:
            raise ObservabilityStoreError(f"读取 trace 失败: {trace_id} ({exc})") from exc
        return data if isinstance(data, dict) else None

    def list_project_traces(self, project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self.traces_dir.exists():
            return []
        traces: list[dict[str, Any]] = []
        for path in sorted(self.traces_dir.glob("trace_*.json"), reverse=True):
            try:
                with path.open("r", encoding="utf-8") as file:
                    item = json.load(file)
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(item, dict) and item.get("project_id") == project_id:
                summary = dict(item)
                summary.pop("events", None)
                traces.append(summary)
            if len(traces) >= limit:
                break
        return traces

    def record_feedback(
        self,
        *,
        trace_id: str,
        project_id: str | None,
        version_id: str | None,
        rating: int,
        category: str,
        comment: str = "",
    ) -> dict[str, Any]:
        item = {
            "feedback_id": f"fb_{uuid.uuid4().hex}",
            "trace_id": trace_id,
            "project_id": project_id,
            "version_id": version_id,
            "rating": rating,
            "category": category,
            "comment": comment,
            "created_at": utc_now_iso(),
        }
        self._append_jsonl(self.feedback_path, item)
        return item

    def metrics_text(self) -> str:
        trace_count = len(list(self.traces_dir.glob("trace_*.json"))) if self.traces_dir.exists() else 0
        event_count = 0
        if self.events_dir.exists():
            for path in self.events_dir.glob("*.jsonl"):
                event_count += len(path.read_text(encoding="utf-8").splitlines())
        feedback_count = len(self.feedback_path.read_text(encoding="utf-8").splitlines()) if self.feedback_path.exists() else 0
        return "\n".join(
            [
                "# HELP midea_workflow_events_total 工作流事件总数",
                "# TYPE midea_workflow_events_total counter",
                f"midea_workflow_events_total {event_count}",
                "# HELP midea_agent_traces_total agent trace 总数",
                "# TYPE midea_agent_traces_total counter",
                f"midea_agent_traces_total {trace_count}",
                "# HELP midea_user_feedback_total 用户反馈总数",
                "# TYPE midea_user_feedback_total counter",
                f"midea_user_feedback_total {feedback_count}",
                "",
            ]
        )

    def _events_path(self, thread_id: str) -> Path:
        _validate_public_id(thread_id, "thread_id")
        return self.events_dir / f"{thread_id}.jsonl"

    def _trace_path(self, trace_id: str) -> Path:
        _validate_public_id(trace_id, "trace_id")
        return self.traces_dir / f"{trace_id}.json"

    def _write_trace(self, trace: dict[str, Any]) -> None:
        trace_id = str(trace.get("trace_id") or "")
        path = self._trace_path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(trace, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp_path, path)

    def _append_jsonl(self, path: Path, item: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as file:
            json.dump(item, file, ensure_ascii=False)
            file.write("\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        items: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ObservabilityStoreError(f"事件 JSONL 解析失败: {path} ({exc})") from exc
            if isinstance(item, dict):
                items.append(item)
        return items


def create_observability_store(root_dir: str | Path | None = None):
    if postgres_runtime_enabled():
        return PostgresObservabilityStore()
    return FileObservabilityStore(root_dir or ROOT_DIR / "projects")


class PostgresObservabilityStore:
    """PostgreSQL 可观测性存储，字段保持 API 公开 ID。"""

    def create_trace(
        self,
        *,
        thread_id: str,
        root_input: str,
        project_id: str | None = None,
        version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.services.postgres_runtime import _connect, _json

        trace = {
            "trace_id": new_trace_id(),
            "thread_id": thread_id,
            "project_id": project_id,
            "version_id": version_id,
            "root_input": root_input,
            "status": "running",
            "started_at": utc_now_iso(),
            "finished_at": None,
            "error": None,
            "metadata": metadata or {},
        }
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_traces (
                    trace_id, thread_id, project_id, version_id, root_input, status, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trace["trace_id"],
                    thread_id,
                    project_id,
                    version_id,
                    root_input,
                    "running",
                    _json(metadata or {}),
                ),
            )
        return trace

    def finish_trace(self, trace_id: str, *, status: str, error: str | None = None) -> dict[str, Any] | None:
        from app.services.postgres_runtime import _connect

        with _connect() as conn:
            conn.execute(
                "UPDATE agent_traces SET status = %s, finished_at = now(), error = %s WHERE trace_id = %s",
                (status, error, trace_id),
            )
        return self.get_trace(trace_id)

    def record_event(self, event: WorkflowEventInput) -> dict[str, Any]:
        from app.services.postgres_runtime import _connect, _json

        item = {
            "event_id": new_event_id(),
            "trace_id": event.trace_id,
            "thread_id": event.thread_id,
            "project_id": event.project_id,
            "version_id": event.version_id,
            "event_type": event.event_type,
            "step": event.step,
            "status": event.status,
            "message": event.message,
            "payload": event.payload or {},
            "created_at": utc_now_iso(),
        }
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_events (
                    event_id, trace_id, thread_id, project_id, version_id,
                    event_type, step, status, message, payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item["event_id"],
                    item["trace_id"],
                    item["thread_id"],
                    item["project_id"],
                    item["version_id"],
                    item["event_type"],
                    item["step"],
                    item["status"],
                    item["message"],
                    _json(item["payload"]),
                ),
            )
        return item

    def list_events(self, thread_id: str, *, after_event_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        from app.services.postgres_runtime import _connect

        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM workflow_events
                WHERE thread_id = %s
                ORDER BY created_at, event_id
                LIMIT 10000
                """,
                (thread_id,),
            ).fetchall()
        items = [_row_to_public_dict(row) for row in rows]
        if after_event_id:
            index = next((idx for idx, item in enumerate(items) if item.get("event_id") == after_event_id), None)
            items = items[index + 1 :] if index is not None else items
        return items[:limit]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        from app.services.postgres_runtime import _connect

        with _connect() as conn:
            trace = conn.execute("SELECT * FROM agent_traces WHERE trace_id = %s", (trace_id,)).fetchone()
            events = conn.execute(
                "SELECT * FROM workflow_events WHERE trace_id = %s ORDER BY created_at, event_id",
                (trace_id,),
            ).fetchall()
        if trace is None:
            return None
        item = _row_to_public_dict(trace)
        item["events"] = [_row_to_public_dict(row) for row in events]
        return item

    def list_project_traces(self, project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        from app.services.postgres_runtime import _connect

        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM agent_traces
                WHERE project_id = %s
                ORDER BY started_at DESC, trace_id DESC
                LIMIT %s
                """,
                (project_id, limit),
            ).fetchall()
        return [_row_to_public_dict(row) for row in rows]

    def record_feedback(
        self,
        *,
        trace_id: str,
        project_id: str | None,
        version_id: str | None,
        rating: int,
        category: str,
        comment: str = "",
    ) -> dict[str, Any]:
        from app.services.postgres_runtime import _connect

        feedback_id = f"fb_{uuid.uuid4().hex}"
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO user_feedback (
                    feedback_id, trace_id, project_id, version_id, rating, category, comment
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (feedback_id, trace_id, project_id, version_id, rating, category, comment),
            )
        return {
            "feedback_id": feedback_id,
            "trace_id": trace_id,
            "project_id": project_id,
            "version_id": version_id,
            "rating": rating,
            "category": category,
            "comment": comment,
            "created_at": utc_now_iso(),
        }

    def metrics_text(self) -> str:
        from app.services.postgres_runtime import _connect

        with _connect() as conn:
            event_count = conn.execute("SELECT count(*) AS count FROM workflow_events").fetchone()["count"]
            trace_count = conn.execute("SELECT count(*) AS count FROM agent_traces").fetchone()["count"]
            feedback_count = conn.execute("SELECT count(*) AS count FROM user_feedback").fetchone()["count"]
        return "\n".join(
            [
                "# HELP midea_workflow_events_total 工作流事件总数",
                "# TYPE midea_workflow_events_total counter",
                f"midea_workflow_events_total {int(event_count)}",
                "# HELP midea_agent_traces_total agent trace 总数",
                "# TYPE midea_agent_traces_total counter",
                f"midea_agent_traces_total {int(trace_count)}",
                "# HELP midea_user_feedback_total 用户反馈总数",
                "# TYPE midea_user_feedback_total counter",
                f"midea_user_feedback_total {int(feedback_count)}",
                "",
            ]
        )


def _validate_public_id(value: str, name: str) -> None:
    if not value or any(char in value for char in "\\/:*?\"<>|"):
        raise ObservabilityStoreError(f"{name} 包含非法路径字符。")


def _row_to_public_dict(row: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for key, value in row.items():
        item[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return item
