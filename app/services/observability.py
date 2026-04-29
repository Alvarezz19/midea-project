from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
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
            "llm_calls": [],
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
        if isinstance(data, dict):
            data["feedback"] = self.list_feedback(trace_id=trace_id)
            return data
        return None

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

    def list_feedback(
        self,
        *,
        trace_id: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items = self._read_jsonl(self.feedback_path)
        filtered: list[dict[str, Any]] = []
        for item in reversed(items):
            if trace_id and item.get("trace_id") != trace_id:
                continue
            if project_id and item.get("project_id") != project_id:
                continue
            filtered.append(item)
            if len(filtered) >= limit:
                break
        return filtered

    def record_llm_call(
        self,
        *,
        trace_id: str,
        provider: str,
        model: str,
        prompt_name: str,
        attempt: int = 1,
        latency_ms: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost: float | None = None,
        status: str = "completed",
        error: str | None = None,
    ) -> dict[str, Any]:
        item = _llm_call_item(
            trace_id=trace_id,
            provider=provider,
            model=model,
            prompt_name=prompt_name,
            attempt=attempt,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            status=status,
            error=error,
        )
        trace = self.get_trace(trace_id)
        if trace is not None:
            calls = trace.setdefault("llm_calls", [])
            if isinstance(calls, list):
                calls.append(item)
            self._write_trace(trace)
        return item

    def metrics_text(self) -> str:
        traces: list[dict[str, Any]] = []
        if self.traces_dir.exists():
            for path in self.traces_dir.glob("trace_*.json"):
                try:
                    with path.open("r", encoding="utf-8") as file:
                        item = json.load(file)
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(item, dict):
                    traces.append(item)
        events: list[dict[str, Any]] = []
        if self.events_dir.exists():
            for path in self.events_dir.glob("*.jsonl"):
                events.extend(self._read_jsonl(path))
        feedback_count = len(self.feedback_path.read_text(encoding="utf-8").splitlines()) if self.feedback_path.exists() else 0
        llm_calls: list[dict[str, Any]] = []
        for trace in traces:
            calls = trace.get("llm_calls")
            if isinstance(calls, list):
                llm_calls.extend(item for item in calls if isinstance(item, dict))
        return _metrics_text_from_items(events, traces, feedback_count, llm_calls)

    def cost_summary(self, *, project_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        traces: list[dict[str, Any]] = []
        if self.traces_dir.exists():
            for path in self.traces_dir.glob("trace_*.json"):
                try:
                    with path.open("r", encoding="utf-8") as file:
                        item = json.load(file)
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(item, dict):
                    traces.append(item)
        llm_calls: list[dict[str, Any]] = []
        for trace in traces:
            calls = trace.get("llm_calls")
            if isinstance(calls, list):
                llm_calls.extend(item for item in calls if isinstance(item, dict))
        return _cost_summary_from_items(llm_calls, traces, project_id=project_id, limit=limit)

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
            llm_calls = conn.execute(
            "SELECT * FROM llm_call_records WHERE trace_id = %s ORDER BY created_at, attempt, id",
                (trace_id,),
            ).fetchall()
            feedback = conn.execute(
                "SELECT * FROM user_feedback WHERE trace_id = %s ORDER BY created_at DESC, feedback_id DESC",
                (trace_id,),
            ).fetchall()
        if trace is None:
            return None
        item = _row_to_public_dict(trace)
        item["events"] = [_row_to_public_dict(row) for row in events]
        item["llm_calls"] = [_row_to_public_dict(row) for row in llm_calls]
        item["feedback"] = [_row_to_public_dict(row) for row in feedback]
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

    def list_feedback(
        self,
        *,
        trace_id: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from app.services.postgres_runtime import _connect

        clauses: list[str] = []
        params: list[Any] = []
        if trace_id:
            clauses.append("trace_id = %s")
            params.append(trace_id)
        if project_id:
            clauses.append("project_id = %s")
            params.append(project_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with _connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM user_feedback
                {where}
                ORDER BY created_at DESC, feedback_id DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        return [_row_to_public_dict(row) for row in rows]

    def record_llm_call(
        self,
        *,
        trace_id: str,
        provider: str,
        model: str,
        prompt_name: str,
        attempt: int = 1,
        latency_ms: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost: float | None = None,
        status: str = "completed",
        error: str | None = None,
    ) -> dict[str, Any]:
        from app.services.postgres_runtime import _connect

        item = _llm_call_item(
            trace_id=trace_id,
            provider=provider,
            model=model,
            prompt_name=prompt_name,
            attempt=attempt,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            status=status,
            error=error,
        )
        with _connect() as conn:
            row = conn.execute(
                """
                INSERT INTO llm_call_records (
                    trace_id, provider, model, prompt_name, attempt, latency_ms,
                    input_tokens, output_tokens, estimated_cost, status, error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    trace_id,
                    item["provider"],
                    item["model"],
                    item["prompt_name"],
                    item["attempt"],
                    item["latency_ms"],
                    item["input_tokens"],
                    item["output_tokens"],
                    item["estimated_cost"],
                    item["status"],
                    item["error"],
                ),
            ).fetchone()
        return _row_to_public_dict(row)

    def metrics_text(self) -> str:
        from app.services.postgres_runtime import _connect

        with _connect() as conn:
            feedback_count = conn.execute("SELECT count(*) AS count FROM user_feedback").fetchone()["count"]
            event_rows = conn.execute("SELECT * FROM workflow_events").fetchall()
            trace_rows = conn.execute("SELECT * FROM agent_traces").fetchall()
            llm_rows = conn.execute("SELECT * FROM llm_call_records").fetchall()
        return _metrics_text_from_items(
            [_row_to_public_dict(row) for row in event_rows],
            [_row_to_public_dict(row) for row in trace_rows],
            int(feedback_count),
            [_row_to_public_dict(row) for row in llm_rows],
        )

    def cost_summary(self, *, project_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        from app.services.postgres_runtime import _connect

        with _connect() as conn:
            trace_rows = conn.execute("SELECT * FROM agent_traces").fetchall()
            llm_rows = conn.execute("SELECT * FROM llm_call_records").fetchall()
        return _cost_summary_from_items(
            [_row_to_public_dict(row) for row in llm_rows],
            [_row_to_public_dict(row) for row in trace_rows],
            project_id=project_id,
            limit=limit,
        )


def _validate_public_id(value: str, name: str) -> None:
    if not value or any(char in value for char in "\\/:*?\"<>|"):
        raise ObservabilityStoreError(f"{name} 包含非法路径字符。")


def _row_to_public_dict(row: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            item[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            item[key] = str(value)
        elif isinstance(value, Decimal):
            item[key] = float(value)
        else:
            item[key] = value
    return item


def _llm_call_item(
    *,
    trace_id: str,
    provider: str,
    model: str,
    prompt_name: str,
    attempt: int,
    latency_ms: int,
    input_tokens: int | None,
    output_tokens: int | None,
    estimated_cost: float | None,
    status: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "llm_call_id": f"llm_{uuid.uuid4().hex}",
        "trace_id": trace_id,
        "provider": provider or "unknown",
        "model": model or "unknown",
        "prompt_name": prompt_name or "unknown",
        "attempt": max(1, int(attempt or 1)),
        "latency_ms": max(0, int(latency_ms or 0)),
        "input_tokens": max(0, int(input_tokens or 0)),
        "output_tokens": max(0, int(output_tokens or 0)),
        "estimated_cost": float(estimated_cost or 0),
        "status": status if status in {"completed", "failed"} else "failed",
        "error": error,
        "created_at": utc_now_iso(),
    }


def _metrics_text_from_items(
    events: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    feedback_count: int,
    llm_calls: list[dict[str, Any]],
) -> str:
    event_status_counts = _count_by(events, "status")
    event_type_counts = _count_by(events, "event_type")
    failed_trace_count = sum(1 for item in traces if item.get("status") in {"failed", "error"})
    export_events = [item for item in events if "export" in str(item.get("event_type") or "")]
    durations = [_trace_duration_ms(item) for item in traces]
    durations = [item for item in durations if item is not None]
    p95_duration = _percentile(durations, 0.95)
    lines = [
        "# HELP midea_workflow_events_total 工作流事件总数",
        "# TYPE midea_workflow_events_total counter",
        f"midea_workflow_events_total {len(events)}",
        "# HELP midea_agent_traces_total agent trace 总数",
        "# TYPE midea_agent_traces_total counter",
        f"midea_agent_traces_total {len(traces)}",
        "# HELP midea_agent_traces_failed_total 失败 trace 总数",
        "# TYPE midea_agent_traces_failed_total counter",
        f"midea_agent_traces_failed_total {failed_trace_count}",
        "# HELP midea_trace_duration_ms_p95 trace 耗时 P95 毫秒",
        "# TYPE midea_trace_duration_ms_p95 gauge",
        f"midea_trace_duration_ms_p95 {p95_duration:.3f}",
        "# HELP midea_user_feedback_total 用户反馈总数",
        "# TYPE midea_user_feedback_total counter",
        f"midea_user_feedback_total {feedback_count}",
        "# HELP midea_llm_calls_total LLM 调用事件总数",
        "# TYPE midea_llm_calls_total counter",
        f"midea_llm_calls_total {len(llm_calls)}",
        "# HELP midea_llm_calls_failed_total LLM 调用失败事件总数",
        "# TYPE midea_llm_calls_failed_total counter",
        f"midea_llm_calls_failed_total {sum(1 for item in llm_calls if item.get('status') == 'failed')}",
        "# HELP midea_llm_input_tokens_total LLM 输入 token 总数",
        "# TYPE midea_llm_input_tokens_total counter",
        f"midea_llm_input_tokens_total {sum(int(item.get('input_tokens') or 0) for item in llm_calls)}",
        "# HELP midea_llm_output_tokens_total LLM 输出 token 总数",
        "# TYPE midea_llm_output_tokens_total counter",
        f"midea_llm_output_tokens_total {sum(int(item.get('output_tokens') or 0) for item in llm_calls)}",
        "# HELP midea_llm_estimated_cost_total LLM 估算成本总额",
        "# TYPE midea_llm_estimated_cost_total counter",
        f"midea_llm_estimated_cost_total {sum(float(item.get('estimated_cost') or 0) for item in llm_calls):.6f}",
        "# HELP midea_project_exports_total 导出事件总数",
        "# TYPE midea_project_exports_total counter",
        f"midea_project_exports_total {len(export_events)}",
        "# HELP midea_project_exports_failed_total 导出失败事件总数",
        "# TYPE midea_project_exports_failed_total counter",
        f"midea_project_exports_failed_total {sum(1 for item in export_events if item.get('status') in {'failed', 'error'})}",
    ]
    lines.extend(_labelled_metric_lines("midea_workflow_events_by_status_total", "status", event_status_counts))
    lines.extend(_labelled_metric_lines("midea_workflow_events_by_type_total", "event_type", event_type_counts))
    lines.append("")
    return "\n".join(lines)


def _cost_summary_from_items(
    llm_calls: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    *,
    project_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    trace_by_id = {str(item.get("trace_id")): item for item in traces if item.get("trace_id")}
    total = _empty_cost_bucket()
    by_project: dict[str, dict[str, Any]] = {}
    by_provider_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_prompt: dict[str, dict[str, Any]] = {}
    by_date: dict[str, dict[str, Any]] = {}

    for raw_call in llm_calls:
        trace = trace_by_id.get(str(raw_call.get("trace_id") or ""), {})
        call_project_id = str(raw_call.get("project_id") or trace.get("project_id") or "unknown")
        if project_id and call_project_id != project_id:
            continue
        provider = str(raw_call.get("provider") or "unknown")
        model = str(raw_call.get("model") or "unknown")
        prompt_name = str(raw_call.get("prompt_name") or "unknown")
        date = _date_bucket(raw_call.get("created_at") or trace.get("started_at"))
        item = {
            "project_id": call_project_id,
            "provider": provider,
            "model": model,
            "prompt_name": prompt_name,
            "date": date,
            "status": raw_call.get("status"),
            "latency_ms": int(raw_call.get("latency_ms") or 0),
            "input_tokens": int(raw_call.get("input_tokens") or 0),
            "output_tokens": int(raw_call.get("output_tokens") or 0),
            "estimated_cost": float(raw_call.get("estimated_cost") or 0),
        }
        _add_cost_item(total, item)
        _add_cost_item(by_project.setdefault(call_project_id, _empty_cost_bucket(project_id=call_project_id)), item)
        _add_cost_item(
            by_provider_model.setdefault((provider, model), _empty_cost_bucket(provider=provider, model=model)),
            item,
        )
        _add_cost_item(by_prompt.setdefault(prompt_name, _empty_cost_bucket(prompt_name=prompt_name)), item)
        _add_cost_item(by_date.setdefault(date, _empty_cost_bucket(date=date)), item)

    return {
        "project_id": project_id,
        "total": _finalize_cost_bucket(total),
        "by_project": _top_cost_buckets(by_project.values(), limit),
        "by_provider_model": _top_cost_buckets(by_provider_model.values(), limit),
        "by_prompt": _top_cost_buckets(by_prompt.values(), limit),
        "by_date": [_finalize_cost_bucket(item) for _, item in sorted(by_date.items())][-limit:],
    }


def _empty_cost_bucket(**labels: Any) -> dict[str, Any]:
    return {
        **labels,
        "calls": 0,
        "failed_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": 0.0,
        "latency_ms_total": 0,
    }


def _add_cost_item(bucket: dict[str, Any], item: dict[str, Any]) -> None:
    bucket["calls"] += 1
    if item.get("status") == "failed":
        bucket["failed_calls"] += 1
    bucket["input_tokens"] += int(item.get("input_tokens") or 0)
    bucket["output_tokens"] += int(item.get("output_tokens") or 0)
    bucket["estimated_cost"] += float(item.get("estimated_cost") or 0)
    bucket["latency_ms_total"] += int(item.get("latency_ms") or 0)


def _finalize_cost_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    item = dict(bucket)
    calls = int(item.get("calls") or 0)
    item["estimated_cost"] = round(float(item.get("estimated_cost") or 0), 6)
    item["average_latency_ms"] = round(float(item.get("latency_ms_total") or 0) / calls, 3) if calls else 0
    item.pop("latency_ms_total", None)
    return item


def _top_cost_buckets(items: Any, limit: int) -> list[dict[str, Any]]:
    finalized = [_finalize_cost_bucket(item) for item in items]
    finalized.sort(key=lambda item: (float(item.get("estimated_cost") or 0), int(item.get("calls") or 0)), reverse=True)
    return finalized[:limit]


def _date_bucket(value: Any) -> str:
    parsed = _parse_datetime(value)
    return parsed.date().isoformat() if parsed is not None else "unknown"


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _labelled_metric_lines(metric: str, label: str, counts: dict[str, int]) -> list[str]:
    return [f'{metric}{{{label}="{_escape_prometheus_label(key)}"}} {value}' for key, value in sorted(counts.items())]


def _escape_prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _trace_duration_ms(trace: dict[str, Any]) -> float | None:
    started = _parse_datetime(trace.get("started_at"))
    finished = _parse_datetime(trace.get("finished_at"))
    if started is None or finished is None:
        return None
    duration = (finished - started).total_seconds() * 1000
    return duration if duration >= 0 else None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]
