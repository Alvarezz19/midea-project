from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.graph.state import AgentState
from app.services.runtime_store import require_postgres_database_url


class PostgresRuntimeError(ValueError):
    """PostgreSQL 运行时存储错误。"""


def _connect():
    import psycopg

    return psycopg.connect(require_postgres_database_url(), row_factory=dict_row)


def _json(value: Any) -> Jsonb:
    return Jsonb(value if value is not None else {})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _version_file_size(path: str | None) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


class PostgresSessionStore:
    """基于 PostgreSQL sessions 表的会话状态存储。"""

    def get(self, thread_id: str) -> AgentState | None:
        _validate_thread_id(thread_id)
        with _connect() as conn:
            row = conn.execute(
                "SELECT graph_state FROM sessions WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        graph_state = row["graph_state"]
        if not isinstance(graph_state, dict):
            raise PostgresRuntimeError(f"会话 graph_state 必须是对象: {thread_id}")
        return graph_state  # type: ignore[return-value]

    def save(self, thread_id: str, state: AgentState) -> None:
        _validate_thread_id(thread_id)
        current_project_id = state.get("current_project_id")
        current_version_id = state.get("current_project_version_id")
        with _connect() as conn:
            project_uuid = _project_uuid(conn, str(current_project_id)) if current_project_id else None
            version_uuid = (
                _version_uuid(conn, str(current_project_id), str(current_version_id))
                if current_project_id and current_version_id
                else None
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    thread_id, project_id, current_version_id, graph_state, status, next_action, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (thread_id) DO UPDATE SET
                    project_id = EXCLUDED.project_id,
                    current_version_id = EXCLUDED.current_version_id,
                    graph_state = EXCLUDED.graph_state,
                    status = EXCLUDED.status,
                    next_action = EXCLUDED.next_action,
                    updated_at = now()
                """,
                (
                    thread_id,
                    project_uuid,
                    version_uuid,
                    _json(dict(state)),
                    str(state.get("status") or "created"),
                    state.get("next_action"),
                ),
            )

    def list_items(self) -> list[tuple[str, AgentState]]:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT thread_id, graph_state FROM sessions ORDER BY updated_at, thread_id"
            ).fetchall()
        return [(str(row["thread_id"]), row["graph_state"]) for row in rows if isinstance(row["graph_state"], dict)]  # type: ignore[list-item]

    def list_states(self) -> list[AgentState]:
        return [state for _, state in self.list_items()]

    def find_by_project_id(self, project_id: str) -> list[AgentState]:
        return [state for _, state in self.find_items_by_project_id(project_id)]

    def find_items_by_project_id(self, project_id: str) -> list[tuple[str, AgentState]]:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT s.thread_id, s.graph_state
                FROM sessions s
                JOIN projects p ON p.id = s.project_id
                WHERE p.public_project_id = %s
                ORDER BY s.updated_at, s.thread_id
                """,
                (project_id,),
            ).fetchall()
        return [(str(row["thread_id"]), row["graph_state"]) for row in rows if isinstance(row["graph_state"], dict)]  # type: ignore[list-item]


def create_initial_version_record(
    metadata: dict[str, Any],
    *,
    project_type: str,
    project_name: str | None = None,
    source_template_id: str | None = None,
) -> None:
    with _connect() as conn:
        project_uuid = _ensure_project(
            conn,
            public_project_id=str(metadata["project_id"]),
            project_type=project_type,
            name=project_name or str(metadata["project_id"]),
            metadata={
                "source_template_id": source_template_id,
                "source_template_path": metadata.get("source_template_path"),
                "summary": metadata.get("summary", {}),
            },
        )
        validation_uuid = _insert_validation_report(
            conn,
            project_uuid=project_uuid,
            version_uuid=None,
            report={"valid": bool(metadata.get("exportable")), "exportable": bool(metadata.get("exportable"))},
        )
        version_uuid = _insert_project_version(
            conn,
            project_uuid=project_uuid,
            metadata=metadata,
            parent_uuid=None,
            patch_record_uuid=None,
            validation_uuid=validation_uuid,
        )
        conn.execute(
            "UPDATE validation_reports SET version_id = %s WHERE id = %s",
            (version_uuid, validation_uuid),
        )
        conn.execute(
            "UPDATE projects SET current_version_id = %s, updated_at = now() WHERE id = %s",
            (version_uuid, project_uuid),
        )
        record_audit_event(
            conn,
            "project_created",
            project_uuid=project_uuid,
            version_uuid=version_uuid,
            payload={"metadata": metadata},
        )


def create_child_version_record(
    metadata: dict[str, Any],
    *,
    patch: dict[str, Any] | None = None,
    risk_level: str = "low",
    request_message: str = "",
    patch_result: dict[str, Any] | None = None,
    validation_report: dict[str, Any] | None = None,
) -> None:
    with _connect() as conn:
        project_uuid = _project_uuid_or_raise(conn, str(metadata["project_id"]))
        parent_uuid = None
        if metadata.get("parent_version_id"):
            parent_uuid = _version_uuid(conn, str(metadata["project_id"]), str(metadata["parent_version_id"]))
        patch_record_uuid = _insert_patch_record(
            conn,
            project_uuid=project_uuid,
            base_version_uuid=parent_uuid,
            patch=patch or metadata.get("patch_summary", {}),
            risk_level=risk_level,
            request_message=request_message,
            result=patch_result or metadata.get("patch_summary", {}),
            status="applied" if (validation_report or {}).get("valid", True) else "failed",
        )
        validation_uuid = _insert_validation_report(
            conn,
            project_uuid=project_uuid,
            version_uuid=None,
            report=validation_report or {},
        )
        version_uuid = _insert_project_version(
            conn,
            project_uuid=project_uuid,
            metadata=metadata,
            parent_uuid=parent_uuid,
            patch_record_uuid=patch_record_uuid,
            validation_uuid=validation_uuid,
        )
        conn.execute(
            "UPDATE validation_reports SET version_id = %s WHERE id = %s",
            (version_uuid, validation_uuid),
        )
        conn.execute(
            "UPDATE projects SET current_version_id = %s, updated_at = now() WHERE id = %s",
            (version_uuid, project_uuid),
        )
        record_audit_event(
            conn,
            "patch_applied",
            project_uuid=project_uuid,
            version_uuid=version_uuid,
            patch_record_uuid=patch_record_uuid,
            payload={"metadata": metadata, "risk_level": risk_level},
        )


def list_project_version_records(project_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT pv.*, parent.version_label AS parent_version_label, p.public_project_id
            FROM project_versions pv
            JOIN projects p ON p.id = pv.project_id
            LEFT JOIN project_versions parent ON parent.id = pv.parent_version_id
            WHERE p.public_project_id = %s
            ORDER BY pv.created_at, pv.version_label
            """,
            (project_id,),
        ).fetchall()
    return [_version_metadata_from_row(row) for row in rows]


def get_project_version_record(project_id: str, version_id: str) -> dict[str, Any]:
    for metadata in list_project_version_records(project_id):
        if metadata.get("version_id") == version_id:
            return metadata
    raise PostgresRuntimeError(f"工程版本不存在: project_id={project_id}, version_id={version_id}")


def current_project_version(project_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT pv.*, parent.version_label AS parent_version_label, p.public_project_id
            FROM projects p
            JOIN project_versions pv ON pv.id = p.current_version_id
            LEFT JOIN project_versions parent ON parent.id = pv.parent_version_id
            WHERE p.public_project_id = %s
            """,
            (project_id,),
        ).fetchone()
    return _version_metadata_from_row(row) if row else None


def set_current_project_version(project_id: str, version_id: str, *, validation_report: dict[str, Any] | None = None) -> dict[str, Any]:
    with _connect() as conn:
        project_uuid = _project_uuid_or_raise(conn, project_id)
        version_uuid = _version_uuid(conn, project_id, version_id)
        if version_uuid is None:
            raise PostgresRuntimeError(f"工程版本不存在: project_id={project_id}, version_id={version_id}")
        conn.execute(
            "UPDATE projects SET current_version_id = %s, updated_at = now() WHERE id = %s",
            (version_uuid, project_uuid),
        )
        validation_uuid = None
        if validation_report is not None:
            validation_uuid = _insert_validation_report(
                conn,
                project_uuid=project_uuid,
                version_uuid=version_uuid,
                report=validation_report,
            )
            conn.execute(
                "UPDATE project_versions SET validation_report_id = %s, exportable = %s WHERE id = %s",
                (validation_uuid, bool(validation_report.get("exportable")), version_uuid),
            )
        record_audit_event(
            conn,
            "project_rolled_back",
            project_uuid=project_uuid,
            version_uuid=version_uuid,
            payload={"target_version_id": version_id},
        )
    metadata = get_project_version_record(project_id, version_id)
    return metadata


def record_project_validation(project_id: str, version_id: str | None, report: dict[str, Any]) -> None:
    with _connect() as conn:
        project_uuid = _project_uuid_or_raise(conn, project_id)
        version_uuid = _version_uuid(conn, project_id, version_id) if version_id else None
        validation_uuid = _insert_validation_report(
            conn,
            project_uuid=project_uuid,
            version_uuid=version_uuid,
            report=report,
        )
        if version_uuid:
            conn.execute(
                "UPDATE project_versions SET validation_report_id = %s, exportable = %s WHERE id = %s",
                (validation_uuid, bool(report.get("exportable")), version_uuid),
            )
        record_audit_event(
            conn,
            "project_validated",
            project_uuid=project_uuid,
            version_uuid=version_uuid,
            payload={"summary": report.get("summary", {})},
        )


def record_project_export(project_id: str, version_id: str | None, report: dict[str, Any]) -> None:
    with _connect() as conn:
        project_uuid = _project_uuid_or_raise(conn, project_id)
        version_uuid = _version_uuid(conn, project_id, version_id) if version_id else None
        record_audit_event(
            conn,
            "project_exported",
            project_uuid=project_uuid,
            version_uuid=version_uuid,
            payload={"exportable": report.get("exportable"), "summary": report.get("summary", {})},
        )


def runtime_counts() -> dict[str, int]:
    tables = ("projects", "project_versions", "patch_records", "validation_reports", "sessions", "audit_events")
    with _connect() as conn:
        result = {}
        for table in tables:
            row = conn.execute(f"SELECT count(*) AS count FROM {table}").fetchone()
            result[table] = int(row["count"]) if row else 0
    return result


def record_audit_event(
    conn: Any,
    event_type: str,
    *,
    project_uuid: Any | None = None,
    version_uuid: Any | None = None,
    patch_record_uuid: Any | None = None,
    session_thread_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_events (
            session_thread_id, project_id, version_id, patch_record_id, event_type, event_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (session_thread_id, project_uuid, version_uuid, patch_record_uuid, event_type, _json(payload or {})),
    )


def _ensure_project(conn: Any, *, public_project_id: str, project_type: str, name: str, metadata: dict[str, Any]) -> Any:
    row = conn.execute(
        """
        INSERT INTO projects (public_project_id, project_type, name, metadata, updated_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (public_project_id) DO UPDATE SET
            project_type = EXCLUDED.project_type,
            name = EXCLUDED.name,
            metadata = projects.metadata || EXCLUDED.metadata,
            updated_at = now()
        RETURNING id
        """,
        (public_project_id, project_type, name, _json(metadata)),
    ).fetchone()
    return row["id"]


def _insert_patch_record(
    conn: Any,
    *,
    project_uuid: Any,
    base_version_uuid: Any | None,
    patch: dict[str, Any],
    risk_level: str,
    request_message: str,
    result: dict[str, Any],
    status: str,
) -> Any:
    row = conn.execute(
        """
        INSERT INTO patch_records (
            project_id, base_version_id, request_message, patch, risk_level, status, result, completed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            project_uuid,
            base_version_uuid,
            request_message,
            _json(patch),
            _normalize_risk_level(risk_level),
            status,
            _json(result),
            _utc_now(),
        ),
    ).fetchone()
    return row["id"]


def _insert_validation_report(conn: Any, *, project_uuid: Any, version_uuid: Any | None, report: dict[str, Any]) -> Any:
    row = conn.execute(
        """
        INSERT INTO validation_reports (
            project_id, version_id, valid, exportable, error_count, warning_count, risk_count, report
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            project_uuid,
            version_uuid,
            bool(report.get("valid")),
            bool(report.get("exportable")),
            int(report.get("error_count") or 0),
            int(report.get("warning_count") or 0),
            int((report.get("summary") or {}).get("risk_count") or 0),
            _json(report),
        ),
    ).fetchone()
    return row["id"]


def _insert_project_version(
    conn: Any,
    *,
    project_uuid: Any,
    metadata: dict[str, Any],
    parent_uuid: Any | None,
    patch_record_uuid: Any | None,
    validation_uuid: Any | None,
) -> Any:
    row = conn.execute(
        """
        INSERT INTO project_versions (
            project_id, parent_version_id, patch_record_id, validation_report_id,
            version_label, json_sha256, file_path, file_size_bytes, exportable, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (project_id, version_label) DO UPDATE SET
            parent_version_id = EXCLUDED.parent_version_id,
            patch_record_id = EXCLUDED.patch_record_id,
            validation_report_id = EXCLUDED.validation_report_id,
            json_sha256 = EXCLUDED.json_sha256,
            file_path = EXCLUDED.file_path,
            file_size_bytes = EXCLUDED.file_size_bytes,
            exportable = EXCLUDED.exportable,
            metadata = EXCLUDED.metadata
        RETURNING id
        """,
        (
            project_uuid,
            parent_uuid,
            patch_record_uuid,
            validation_uuid,
            str(metadata["version_id"]),
            str(metadata["json_sha256"]),
            str(metadata["version_path"]),
            _version_file_size(str(metadata.get("version_path"))),
            bool(metadata.get("exportable")),
            _json({key: value for key, value in metadata.items() if key not in {"project_id", "version_id", "parent_version_id", "version_path", "json_sha256", "exportable", "created_at"}}),
        ),
    ).fetchone()
    return row["id"]


def _project_uuid(conn: Any, public_project_id: str) -> Any | None:
    row = conn.execute(
        "SELECT id FROM projects WHERE public_project_id = %s",
        (public_project_id,),
    ).fetchone()
    return row["id"] if row else None


def _project_uuid_or_raise(conn: Any, public_project_id: str) -> Any:
    project_uuid = _project_uuid(conn, public_project_id)
    if project_uuid is None:
        raise PostgresRuntimeError(f"项目不存在: {public_project_id}")
    return project_uuid


def _version_uuid(conn: Any, public_project_id: str, version_label: str | None) -> Any | None:
    if not version_label:
        return None
    row = conn.execute(
        """
        SELECT pv.id
        FROM project_versions pv
        JOIN projects p ON p.id = pv.project_id
        WHERE p.public_project_id = %s AND pv.version_label = %s
        """,
        (public_project_id, version_label),
    ).fetchone()
    return row["id"] if row else None


def _version_metadata_from_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    created_at = row.get("created_at")
    return {
        **metadata,
        "project_id": row.get("public_project_id"),
        "version_id": row.get("version_label"),
        "parent_version_id": row.get("parent_version_label"),
        "version_path": row.get("file_path"),
        "json_sha256": row.get("json_sha256"),
        "exportable": row.get("exportable"),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


def _normalize_risk_level(value: str) -> str:
    return value if value in {"low", "medium", "high", "blocked"} else "low"


def _validate_thread_id(thread_id: str) -> None:
    if not thread_id or any(char in thread_id for char in "\\/:*?\"<>|"):
        raise PostgresRuntimeError("thread_id 包含非法路径字符。")
