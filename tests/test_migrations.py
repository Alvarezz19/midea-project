from __future__ import annotations

import re
from pathlib import Path


POSTGRES_INITIAL_SCHEMA = Path("migrations/postgres/001_initial_schema.sql")
POSTGRES_RUNTIME_PUBLIC_IDS = Path("migrations/postgres/002_runtime_public_ids.sql")
POSTGRES_OBSERVABILITY = Path("migrations/postgres/003_observability.sql")


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


def test_postgres_runtime_public_ids_migration_keeps_api_ids_stable() -> None:
    sql = POSTGRES_RUNTIME_PUBLIC_IDS.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS public_project_id text" in sql
    assert "idx_projects_public_project_id" in sql
    assert "002_runtime_public_ids" in sql


def test_postgres_observability_migration_tracks_stage7_contract() -> None:
    sql = POSTGRES_OBSERVABILITY.read_text(encoding="utf-8")
    required_tables = {"workflow_events", "agent_traces", "llm_call_records", "user_feedback"}

    created_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", sql))

    assert required_tables <= created_tables
    assert "event_id text PRIMARY KEY" in sql
    assert "trace_id text NOT NULL" in sql
    assert "thread_id text NOT NULL" in sql
    assert "payload jsonb NOT NULL" in sql
    assert "rating BETWEEN 1 AND 5" in sql
    assert "003_observability" in sql
