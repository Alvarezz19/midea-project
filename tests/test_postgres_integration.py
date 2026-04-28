from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1" or not os.getenv("DATABASE_URL"),
    reason="需要显式设置 RUN_POSTGRES_INTEGRATION=1 和 DATABASE_URL。",
)


def test_postgres_business_schema_migration_smoke() -> None:
    import psycopg

    _apply_postgres_migrations()
    required_tables = {
        "projects",
        "project_versions",
        "patch_records",
        "validation_reports",
        "sessions",
        "audit_events",
    }
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            """,
            (list(required_tables),),
        ).fetchall()
        public_id_exists = conn.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'projects'
              AND column_name = 'public_project_id'
            """
        ).fetchone()

    assert {row[0] for row in rows} == required_tables
    assert public_id_exists is not None


def test_langgraph_postgres_checkpointer_restores_state_across_processes() -> None:
    _apply_postgres_migrations()
    thread_id = f"pytest-postgres-checkpoint-{uuid.uuid4().hex}"
    write_env = _postgres_env(setup="true")
    read_env = _postgres_env(setup="false")

    write_code = f"""
from app.graph.state import initial_state
from app.graph.workflow import invoke_workflow

state = initial_state('我要做一个控制程序')
state['versions_dir'] = 'projects/versions'
result = invoke_workflow(state, thread_id={thread_id!r})
print(result['status'])
print(result['next_action'])
"""
    write_result = subprocess.run(
        [sys.executable, "-c", write_code],
        cwd=ROOT_DIR,
        env=write_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert write_result.stdout.splitlines()[-2:] == ["need_project_type", "ask_project_type"]

    read_code = f"""
from app.graph.workflow import get_workflow

snapshot = get_workflow().get_state({{'configurable': {{'thread_id': {thread_id!r}}}}})
print(snapshot.values.get('status'))
print(snapshot.values.get('next_action'))
"""
    read_result = subprocess.run(
        [sys.executable, "-c", read_code],
        cwd=ROOT_DIR,
        env=read_env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert read_result.stdout.splitlines()[-2:] == ["need_project_type", "ask_project_type"]


def test_postgres_runtime_store_persists_session_versions_patch_validation_and_audit() -> None:
    _apply_postgres_migrations()
    project_marker = f"pytest_pg_runtime_{uuid.uuid4().hex}"
    env = _postgres_env(setup="true")
    env["RUNTIME_STORE_BACKEND"] = "postgres"

    code = f"""
from fastapi.testclient import TestClient

from app.main import app
from app.services.json_project import load_project

client = TestClient(app)
created = client.post('/api/sessions', json={{'project_type': 'ahu', 'auto_confirm_template': True}})
created.raise_for_status()
thread_id = created.json()['thread_id']

ready = client.post(
    f'/api/sessions/{{thread_id}}/message',
    json={{'message': '我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯，标记 {project_marker}。'}},
)
ready.raise_for_status()
state = ready.json()['state']
project_id = state['current_project_id']
first_version_id = state['current_project_version_id']
project_path = state['current_project_path']
nodes = load_project(project_path)
target = next(node for node in nodes if isinstance(node, dict) and node.get('type') not in {{'tab', 'subflow'}})
patch = {{'op': 'rename_node', 'node_selector': {{'id': target['id']}}, 'new_name': 'PG运行时存储验收-{project_marker}'}}

patched = client.post(
    f'/api/sessions/{{thread_id}}/message',
    json={{'message': '执行 PostgreSQL 运行时存储补丁验收。', 'pending_patch': patch}},
)
patched.raise_for_status()
patched_state = patched.json()['state']
second_version_id = patched_state['current_project_version_id']

versions = client.get(f'/api/projects/{{project_id}}/versions')
versions.raise_for_status()
validate = client.post(f'/api/projects/{{project_id}}/validate')
validate.raise_for_status()
rollback = client.post(f'/api/projects/{{project_id}}/rollback', json={{'target_version_id': first_version_id}})
rollback.raise_for_status()

print(project_id)
print(first_version_id)
print(second_version_id)
print(len(versions.json()['versions']))
print(rollback.json()['state']['current_project_version_id'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    project_id, first_version_id, second_version_id, version_count, rollback_version_id = lines[-5:]
    assert project_id.startswith("project_")
    assert first_version_id != second_version_id
    assert version_count == "2"
    assert rollback_version_id == first_version_id

    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT count(*) FROM sessions s JOIN projects p ON p.id = s.project_id WHERE p.public_project_id = %s) AS session_count,
                (SELECT count(*) FROM project_versions pv JOIN projects p ON p.id = pv.project_id WHERE p.public_project_id = %s) AS version_count,
                (SELECT count(*) FROM patch_records pr JOIN projects p ON p.id = pr.project_id WHERE p.public_project_id = %s) AS patch_count,
                (SELECT count(*) FROM validation_reports vr JOIN projects p ON p.id = vr.project_id WHERE p.public_project_id = %s) AS validation_count,
                (SELECT count(*) FROM audit_events ae JOIN projects p ON p.id = ae.project_id WHERE p.public_project_id = %s) AS audit_count,
                (SELECT pv.version_label FROM projects p JOIN project_versions pv ON pv.id = p.current_version_id WHERE p.public_project_id = %s) AS current_version
            """,
            (project_id, project_id, project_id, project_id, project_id, project_id),
        ).fetchone()

    assert row is not None
    assert row[0] == 1
    assert row[1] == 2
    assert row[2] == 1
    assert row[3] >= 3
    assert row[4] >= 4
    assert row[5] == first_version_id


def _postgres_env(*, setup: str) -> dict[str, str]:
    env = dict(os.environ)
    env["LANGGRAPH_CHECKPOINTER_BACKEND"] = "postgres"
    env["LANGGRAPH_CHECKPOINTER_SETUP"] = setup
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _apply_postgres_migrations() -> None:
    import psycopg

    migration_dir = ROOT_DIR / "migrations" / "postgres"
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for path in sorted(migration_dir.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
