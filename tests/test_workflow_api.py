from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.graph.state import initial_state
from app.graph.workflow import get_workflow, invoke_workflow
from app.main import app
from app.services.json_project import load_project
from app.services.retrieval import search_nodes, search_templates
from app.services.session_store import FileSessionStore
from app.services.validator import validate_project


def test_workflow_waits_for_project_type(tmp_path: Path) -> None:
    state = initial_state("我要做一个控制程序")
    state["versions_dir"] = str(tmp_path)

    result = invoke_workflow(state)

    assert result["status"] == "need_project_type"
    assert result["next_action"] == "ask_project_type"
    assert result["template_candidates"] == []
    assert "请先确认项目类型" in result["messages"][-1]["content"]


def test_workflow_reuses_compiled_graph_singleton() -> None:
    assert get_workflow() is get_workflow()


def test_workflow_saves_checkpoint_with_thread_id(tmp_path: Path) -> None:
    thread_id = "pytest-checkpoint-thread"
    state = initial_state("我要做一个控制程序")
    state["versions_dir"] = str(tmp_path)

    result = invoke_workflow(state, thread_id=thread_id)
    snapshot = get_workflow().get_state({"configurable": {"thread_id": thread_id}})

    assert snapshot.values["status"] == result["status"]
    assert snapshot.values["next_action"] == "ask_project_type"


def test_workflow_can_create_project_version(tmp_path: Path) -> None:
    state = initial_state("我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)

    result = invoke_workflow(state)

    assert result["status"] == "project_version_ready"
    assert result["project_type"] == "ahu"
    assert result["selected_template_id"]
    assert result["current_project_version_id"]
    assert result["current_project_path"]
    assert Path(result["current_project_path"]).exists()
    assert validate_project(load_project(result["current_project_path"]))["valid"]


def test_workflow_applies_pending_patch(tmp_path: Path) -> None:
    best = search_templates("风冷热泵 水泵 旁通阀", project_type="plant_room", limit=1)[0]
    node = search_nodes("水泵 比较判断", template_id=best["template_id"], tab_label_contains="水泵", node_type="compare", limit=1)[0]
    patch = {"op": "rename_node", "node_selector": {"id": node["node_id"]}, "new_name": "工作流测试-水泵比较节点"}

    state = initial_state("我要做一个风冷热泵机房群控程序，包含水泵和旁通阀控制", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    state["pending_patch"] = patch

    result = invoke_workflow(state)

    assert result["status"] == "patch_applied"
    assert result["validation_report"]["valid"]
    assert result["patch_result"]["changed"]
    assert result["current_project_version_id"] != state.get("current_project_version_id")
    nodes = load_project(result["current_project_path"])
    renamed = next(item for item in nodes if item.get("id") == node["node_id"])
    assert renamed["name"] == "工作流测试-水泵比较节点"


def test_workflow_plans_and_applies_natural_language_patch(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序，包含水泵和旁通阀控制", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"
    original_candidates = created["template_candidates"]
    original_path = created["current_project_path"]
    original_version_id = created["current_project_version_id"]

    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把节点 3a4c97e 改名为 工作流规划-水泵比较节点"}]
    result = invoke_workflow(created)

    assert result["status"] == "patch_applied"
    assert result["template_candidates"] == original_candidates
    assert result["current_project_version_id"] != original_version_id
    assert result["current_project_path"] != original_path
    assert result["planner_result"]["status"] == "planned"
    assert result["validation_report"]["valid"]
    assert next(item for item in load_project(original_path) if item.get("id") == "3a4c97e").get("name") != "工作流规划-水泵比较节点"
    nodes = load_project(result["current_project_path"])
    renamed = next(item for item in nodes if item.get("id") == "3a4c97e")
    assert renamed["name"] == "工作流规划-水泵比较节点"


def test_workflow_returns_clarification_when_planner_is_ambiguous(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"

    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把比较判断改名为 不应自动执行"}]
    result = invoke_workflow(created)

    assert result["status"] == "awaiting_patch_clarification"
    assert result["next_action"] == "clarify_patch"
    assert result["planner_result"]["status"] == "needs_clarification"
    assert "需要补充信息" in result["messages"][-1]["content"]


def test_workflow_reports_ambiguous_patch(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    state["pending_patch"] = {"op": "rename_node", "node_selector": {"type": "compare"}, "new_name": "不应执行"}

    result = invoke_workflow(state)

    assert result["status"] == "patch_failed"
    assert result["next_action"] == "revise_patch"
    assert "必须唯一" in result["error"]


def test_api_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "session_store", FileSessionStore(tmp_path / "sessions"))
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert "工程 JSON 智能体工作台" in page.text

    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "createSession" in script.text

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    search = client.post("/api/templates/search", json={"query": "风冷热泵 水泵 旁通阀", "project_type": "plant_room", "limit": 2})
    assert search.status_code == 200
    assert search.json()["items"][0]["file_name"] == "风冷热泵标准控制程序[风冷涡旋]20240905.json"

    knowledge = client.post("/api/knowledge/search", json={"query": "AHU 防冻保护 新风阀", "limit": 3})
    assert knowledge.status_code == 200
    assert knowledge.json()["items"]
    assert knowledge.json()["items"][0]["source_path"] == "knowledge/AHU控制策略.md"

    created = client.post("/api/sessions", json={"auto_confirm_template": True, "versions_dir": str(tmp_path)})
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]

    message = client.post(
        f"/api/sessions/{thread_id}/message",
        json={"message": "我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯"},
    )
    assert message.status_code == 200
    state = message.json()["state"]
    assert state["status"] == "project_version_ready"
    assert Path(state["current_project_path"]).exists()

    validate = client.post("/api/projects/validate", json={"path": state["current_project_path"]})
    assert validate.status_code == 200
    assert validate.json()["valid"]

    validate_by_id = client.post(f"/api/projects/{state['current_project_id']}/validate")
    assert validate_by_id.status_code == 200
    assert validate_by_id.json()["valid"]

    candidate = state["template_candidates"][0]
    plan_node = search_nodes("比较判断", template_id=candidate["template_id"], node_type="compare", limit=1)[0]
    plan = client.post(
        "/api/planner/plan",
        json={
            "message": f"把节点 {plan_node['node_id']} 改名为 API规划-比较节点",
            "project_path": state["current_project_path"],
            "template_id": candidate["template_id"],
            "project_type": state["project_type"],
        },
    )
    assert plan.status_code == 200
    assert plan.json()["status"] == "planned"
    assert plan.json()["pending_patch"]["op"] == "rename_node"

    dry_run = client.post(
        "/api/planner/dry-run",
        json={
            "message": f"把节点 {plan_node['node_id']} 改名为 API dry-run-比较节点",
            "project_path": state["current_project_path"],
            "template_id": candidate["template_id"],
            "project_type": state["project_type"],
        },
    )
    assert dry_run.status_code == 200
    dry_run_data = dry_run.json()
    assert dry_run_data["status"] == "dry_run_valid"
    assert dry_run_data["dry_run"]["saved"] is False
    assert dry_run_data["dry_run"]["diff"]["summary"]["modified_count"] == 1
    assert dry_run_data["dry_run"]["diff"]["affected_node_ids"] == [plan_node["node_id"]]
    assert "nodes" not in dry_run_data["dry_run"]

    export = client.get("/api/projects/export", params={"path": state["current_project_path"]})
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/json")
    assert len(export.content) > 1000

    export_by_id = client.get(f"/api/projects/{state['current_project_id']}/export")
    assert export_by_id.status_code == 200
    assert export_by_id.headers["content-type"].startswith("application/json")
    assert export_by_id.content == export.content

    node = search_nodes("比较判断", template_id=candidate["template_id"], node_type="compare", limit=1)[0]
    patch = {"op": "rename_node", "node_selector": {"id": node["node_id"]}, "new_name": "API测试-比较节点"}
    patched = client.post(
        f"/api/sessions/{thread_id}/message",
        json={"message": "把选中的比较节点改名", "pending_patch": patch},
    )
    assert patched.status_code == 200
    patched_state = patched.json()["state"]
    assert patched_state["status"] == "patch_applied"
    assert patched_state["validation_report"]["valid"]
    assert len(patched_state["patch_result"]["changes"]) == 1
    assert patched_state["patch_result"]["diff"]["summary"]["modified_count"] == 1
    assert patched_state["current_project_version_id"] != state["current_project_version_id"]

    versions = client.get(f"/api/projects/{state['current_project_id']}/versions")
    assert versions.status_code == 200
    version_items = versions.json()["versions"]
    assert [item["version_id"] for item in version_items] == [state["current_project_version_id"], patched_state["current_project_version_id"]]
    assert version_items[1]["parent_version_id"] == state["current_project_version_id"]

    rollback = client.post(
        f"/api/projects/{state['current_project_id']}/rollback",
        json={"target_version_id": state["current_project_version_id"]},
    )
    assert rollback.status_code == 200
    rollback_state = rollback.json()["state"]
    assert rollback_state["status"] == "rolled_back"
    assert rollback_state["current_project_version_id"] == state["current_project_version_id"]
    assert rollback_state["current_project_path"] == state["current_project_path"]

    after_rollback_versions = client.get(f"/api/projects/{state['current_project_id']}/versions")
    assert after_rollback_versions.status_code == 200
    assert [item["version_id"] for item in after_rollback_versions.json()["versions"]] == [
        state["current_project_version_id"],
        patched_state["current_project_version_id"],
    ]

    export_after_rollback = client.get(f"/api/projects/{state['current_project_id']}/export")
    assert export_after_rollback.status_code == 200
    assert export_after_rollback.content == export.content
