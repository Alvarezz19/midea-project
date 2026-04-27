from __future__ import annotations

import re
from pathlib import Path


POSTGRES_INITIAL_SCHEMA = Path("migrations/postgres/001_initial_schema.sql")


def test_postgres_initial_schema_contains_required_tables() -> None:
    sql = POSTGRES_INITIAL_SCHEMA.read_text(encoding="utf-8")
    required_tables = {
        "users",
        "projects",
        "project_versions",
        "patch_records",
        "validation_reports",
        "sessions",
        "audit_events",
        "template_catalog",
        "tab_index",
        "node_index",
        "block_index",
        "knowledge_chunks",
    }

    created_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", sql))

    assert required_tables <= created_tables
    assert "schema_migrations" in created_tables


def test_postgres_initial_schema_tracks_versions_and_audit_links() -> None:
    sql = POSTGRES_INITIAL_SCHEMA.read_text(encoding="utf-8")

    assert "parent_version_id uuid REFERENCES project_versions(id)" in sql
    assert "json_sha256 char(64) NOT NULL" in sql
    assert "patch_record_id uuid REFERENCES patch_records(id)" in sql
    assert "validation_report_id uuid REFERENCES validation_reports(id)" in sql
    assert "event_type text NOT NULL" in sql
    assert "session_thread_id text REFERENCES sessions(thread_id)" in sql


def test_postgres_initial_schema_uses_input_sources_term() -> None:
    sql = POSTGRES_INITIAL_SCHEMA.read_text(encoding="utf-8")

    assert "input_sources jsonb NOT NULL" in sql
    assert "wires_to" not in sql
