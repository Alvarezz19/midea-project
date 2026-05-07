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


def test_langgraph_postgres_interrupt_resume_applies_patch_across_processes(tmp_path: Path) -> None:
    _apply_postgres_migrations()
    thread_id = f"pytest-postgres-interrupt-{uuid.uuid4().hex}"
    versions_dir = str(tmp_path / "versions")
    write_env = _postgres_env(setup="true")
    read_env = _postgres_env(setup="false")

    write_code = f"""
from app.graph.state import initial_state
from app.graph.workflow import invoke_workflow

state = initial_state('我要做一个风冷热泵机房群控程序，包含水泵和旁通阀控制', auto_confirm_template=True)
state['versions_dir'] = {versions_dir!r}
created = invoke_workflow(state, thread_id={thread_id!r})
patch = {{'op': 'disconnect', 'target_node_selector': {{'id': '3a4c97e'}}, 'target_input': 0}}
created['messages'] = list(created['messages']) + [{{'role': 'user', 'content': '断开比较节点输入，验证 interrupt。'}}]
created['pending_patch'] = patch
interrupted = invoke_workflow(created, thread_id={thread_id!r})
print(interrupted['status'])
print(interrupted['next_action'])
print(interrupted['__interrupt__'][0].value['kind'])
print(interrupted['current_project_version_id'])
"""
    write_result = subprocess.run(
        [sys.executable, "-c", write_code],
        cwd=ROOT_DIR,
        env=write_env,
        check=True,
        capture_output=True,
        text=True,
    )
    status, next_action, interrupt_kind, first_version_id = write_result.stdout.splitlines()[-4:]
    assert [status, next_action, interrupt_kind] == ["awaiting_patch_confirmation", "confirm_patch", "patch_confirmation"]

    read_code = f"""
from app.graph.workflow import invoke_workflow_resume

resumed = invoke_workflow_resume({{'action': 'approve'}}, thread_id={thread_id!r})
print(resumed['status'])
print(resumed['validation_report']['valid'])
print(resumed['patch_confirmation']['action'])
print(resumed['patch_confirmation']['confirmed_project_version_id'])
print(resumed['current_project_version_id'])
"""
    read_result = subprocess.run(
        [sys.executable, "-c", read_code],
        cwd=ROOT_DIR,
        env=read_env,
        check=True,
        capture_output=True,
        text=True,
    )
    resumed_status, valid, action, confirmed_version_id, second_version_id = read_result.stdout.splitlines()[-5:]

    assert resumed_status == "patch_applied"
    assert valid == "True"
    assert action == "approved"
    assert confirmed_version_id == first_version_id
    assert second_version_id != first_version_id


def test_postgres_runtime_store_persists_session_versions_patch_validation_and_audit() -> None:
    _apply_postgres_migrations()
    project_marker = f"pytest_pg_runtime_{uuid.uuid4().hex}"
    env = _postgres_env(setup="true")
    env["RUNTIME_STORE_BACKEND"] = "postgres"

    code = f"""
from fastapi.testclient import TestClient

from app.main import app
client = TestClient(app)
created = client.post('/api/sessions', json={{'project_type': 'plant_room', 'auto_confirm_template': True}})
created.raise_for_status()
thread_id = created.json()['thread_id']

ready = client.post(
    f'/api/sessions/{{thread_id}}/message',
    json={{'message': '我要做风冷热泵机房群控程序，包含水泵和旁通阀控制，标记 {project_marker}。'}},
)
ready.raise_for_status()
state = ready.json()['state']
project_id = state['current_project_id']
first_version_id = state['current_project_version_id']
patch = {{'op': 'disconnect', 'target_node_selector': {{'id': '3a4c97e'}}, 'target_input': 0}}

pending = client.post(
    f'/api/sessions/{{thread_id}}/message',
    json={{'message': '执行 PostgreSQL 运行时存储中风险补丁验收。', 'pending_patch': patch}},
)
pending.raise_for_status()
pending_state = pending.json()['state']
assert pending_state['status'] == 'awaiting_patch_confirmation'
assert pending_state['next_action'] == 'confirm_patch'

approved = client.post(f'/api/sessions/{{thread_id}}/patch-confirmation', json={{'action': 'approve'}})
approved.raise_for_status()
patched_state = approved.json()['state']
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
