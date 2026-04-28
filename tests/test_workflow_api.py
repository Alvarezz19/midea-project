from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_workflow_plans_and_applies_replace_constant_patch(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序，包含水泵和旁通阀控制", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"

    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把节点 67febfa 的常量值改为 6"}]
    result = invoke_workflow(created)

    assert result["status"] == "patch_applied"
    assert result["planner_result"]["pending_patch"]["op"] == "replace_constant"
    assert result["risk_assessment"]["risk_level"] == "low"
    assert result["risk_assessment"]["requires_confirmation"] is False
    nodes = load_project(result["current_project_path"])
    assert next(item for item in nodes if item.get("id") == "67febfa")["fixedValue"] == "6"


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


def test_workflow_uses_llm_planner_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"

    def fake_llm_dry_run(
        message: str,
        *,
        project_path: str,
        template_id: str | None = None,
        project_type: str | None = None,
        provider: str | None = None,
        llm_max_attempts: int = 2,
    ) -> dict[str, Any]:
        assert "比较判断改名" in message
        assert project_path == created["current_project_path"]
        assert template_id == created["selected_template_id"]
        assert project_type == "plant_room"
        assert provider == "deepseek"
        assert llm_max_attempts == 2
        pending_patch = {"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": "工作流LLM-比较节点"}
        return {
            "status": "dry_run_valid",
            "planner_result": {
                "status": "planned",
                "planner": "llm",
                "risk_level": "low",
                "pending_patch": pending_patch,
                "questions": [],
            },
            "pending_patch": pending_patch,
            "dry_run": {"saved": False, "valid": True, "diff": {"summary": {"modified_count": 1}}},
            "planner_attempts": [{"attempt": 1, "planner_status": "planned", "risk_level": "low", "operation_count": 1}],
        }

    monkeypatch.setattr("app.graph.nodes.plan_patch_with_llm_dry_run_feedback", fake_llm_dry_run)
    created["use_llm_planner"] = True
    created["llm_provider"] = "deepseek"
    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "请把比较判断改名"}]

    result = invoke_workflow(created)

    assert result["status"] == "patch_applied"
    assert result["planner_result"]["planner"] == "llm"
    assert result["planner_attempts"][0]["planner_status"] == "planned"
    nodes = load_project(result["current_project_path"])
    renamed = next(item for item in nodes if item.get("id") == "3a4c97e")
    assert renamed["name"] == "工作流LLM-比较节点"


def test_workflow_llm_planner_retries_schema_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"

    calls: list[list[dict[str, str]]] = []

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        del provider
        calls.append(messages)
        if len(calls) == 1:
            return {
                "status": "planned",
                "intent": "delete_logic",
                "summary": "错误地输出未知操作。",
                "risk_level": "high",
                "risk_reasons": ["未知操作"],
                "required_context": [],
                "operations": [{"op": "delete_node", "node_selector": {"id": "3a4c97e"}}],
                "validation_expectations": [],
                "questions": [],
            }
        assert "未通过 schema 校验" in messages[1]["content"]
        return {
            "status": "planned",
            "intent": "modify_existing_logic",
            "summary": "改名目标节点。",
            "risk_level": "low",
            "risk_reasons": [],
            "required_context": [],
            "operations": [{"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": "工作流schema重试-比较节点"}],
            "validation_expectations": ["目标节点唯一"],
            "questions": [],
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    created["use_llm_planner"] = True
    created["llm_max_attempts"] = 2
    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把节点 3a4c97e 改名"}]

    result = invoke_workflow(created)

    assert result["status"] == "patch_applied"
    assert result["planner_result"]["planner"] == "llm"
    assert result["planner_result"]["planner_attempt_count"] == 2
    assert len(calls) == 2
    nodes = load_project(result["current_project_path"])
    renamed = next(item for item in nodes if item.get("id") == "3a4c97e")
    assert renamed["name"] == "工作流schema重试-比较节点"


def test_workflow_llm_planner_returns_clarification_after_feedback_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services.planner_execution import PlannerDryRunFeedbackError

    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"

    def fake_llm_dry_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise PlannerDryRunFeedbackError(
            {
                "message": "LLM planner dry-run 重试后仍失败。",
                "last_error": "第 0 个操作匹配到 12 个节点，必须唯一。",
                "planner_attempts": [{"attempt": 1, "planner_status": "planned", "dry_run_error": "必须唯一"}],
                "planner_result": {
                    "status": "planned",
                    "planner": "llm",
                    "questions": [],
                },
            }
        )

    monkeypatch.setattr("app.graph.nodes.plan_patch_with_llm_dry_run_feedback", fake_llm_dry_run)
    created["use_llm_planner"] = True
    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "把比较判断改名为 不应执行"}]

    result = invoke_workflow(created)

    assert result["status"] == "awaiting_patch_clarification"
    assert result["next_action"] == "clarify_patch"
    assert result["pending_patch"] is None
    assert result["planner_attempts"][0]["dry_run_error"] == "必须唯一"
    assert "必须唯一" in result["messages"][-1]["content"]


def test_workflow_llm_planner_requires_confirmation_for_non_low_risk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    created = invoke_workflow(state)
    assert created["status"] == "project_version_ready"

    def fake_llm_dry_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        pending_patch = {"op": "disconnect", "target_node_selector": {"id": "3a4c97e"}, "target_input": 0}
        return {
            "status": "dry_run_valid",
            "planner_result": {
                "status": "planned",
                "planner": "llm",
                "risk_level": "medium",
                "pending_patch": pending_patch,
                "questions": [],
            },
            "pending_patch": pending_patch,
            "dry_run": {
                "saved": False,
                "valid": True,
                "diff": {"summary": {"affected_node_count": 1}},
            },
            "planner_attempts": [{"attempt": 1, "planner_status": "planned", "risk_level": "medium", "operation_count": 1}],
        }

    monkeypatch.setattr("app.graph.nodes.plan_patch_with_llm_dry_run_feedback", fake_llm_dry_run)
    created["use_llm_planner"] = True
    created["messages"] = list(created["messages"]) + [{"role": "user", "content": "断开一个输入"}]

    result = invoke_workflow(created)

    assert result["status"] == "awaiting_patch_confirmation"
    assert result["next_action"] == "confirm_patch"
    assert result["pending_patch"] is None
    assert result["pending_confirmation_patch"] == {"op": "disconnect", "target_node_selector": {"id": "3a4c97e"}, "target_input": 0}
    assert result["risk_assessment"]["requires_confirmation"] is True
    assert result["planner_dry_run"]["valid"]
    assert "需要确认后再应用" in result["messages"][-1]["content"]


def test_workflow_requires_confirmation_for_manual_medium_risk_patch(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    state["pending_patch"] = {"op": "disconnect", "target_node_selector": {"id": "3a4c97e"}, "target_input": 0}

    result = invoke_workflow(state)

    assert result["status"] == "awaiting_patch_confirmation"
    assert result["next_action"] == "confirm_patch"
    assert result["pending_patch"] is None
    assert result["pending_confirmation_patch"]["op"] == "disconnect"
    assert result["planner_dry_run"]["valid"]
    assert result["risk_assessment"]["risk_level"] == "medium"


def test_workflow_requires_confirmation_for_dynamic_input_patch(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    state["pending_patch"] = {"op": "enable_dynamic_input", "node_selector": {"id": "f22a5df"}}

    result = invoke_workflow(state)

    assert result["status"] == "awaiting_patch_confirmation"
    assert result["next_action"] == "confirm_patch"
    assert result["pending_patch"] is None
    assert result["pending_confirmation_patch"]["op"] == "enable_dynamic_input"
    assert result["risk_assessment"]["risk_level"] == "medium"
    assert result["planner_dry_run"]["diff"]["summary"]["modified_count"] == 1


def test_workflow_requires_high_risk_confirmation_for_copy_block(tmp_path: Path) -> None:
    state = initial_state("我要做一个风冷热泵机房群控程序", project_type="plant_room", auto_confirm_template=True)
    state["versions_dir"] = str(tmp_path)
    state["pending_patch"] = {
        "op": "copy_block",
        "block_id": "block_3f996bfafceb",
        "target_tab_selector": {"label": "旁通阀控制"},
        "x_offset": 80,
        "y_offset": 80,
        "name_prefix": "复制-",
    }

    result = invoke_workflow(state)

    assert result["status"] == "awaiting_patch_confirmation"
    assert result["next_action"] == "confirm_patch"
    assert result["pending_patch"] is None
    assert result["pending_confirmation_patch"]["op"] == "copy_block"
    assert result["risk_assessment"]["risk_level"] == "high"
    assert result["planner_dry_run"]["diff"]["summary"]["added_count"] == 17


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
    template_id = search.json()["items"][0]["template_id"]

    blocks = client.post("/api/blocks/search", json={"query": "水泵控制", "template_id": template_id, "limit": 3})
    assert blocks.status_code == 200
    block_items = blocks.json()["items"]
    assert block_items
    assert block_items[0]["function_type"] == "pump_control"
    assert block_items[0]["node_ids"]
    block_context = client.post(
        "/api/blocks/context",
        json={"block_id": block_items[0]["block_id"], "max_nodes": 6, "max_chars": 8000},
    )
    assert block_context.status_code == 200
    block_context_data = block_context.json()
    assert block_context_data["block"]["block_id"] == block_items[0]["block_id"]
    assert 1 <= len(block_context_data["nodes"]) <= 6
    assert "internal_edges" in block_context_data
    assert "budget" in block_context_data

    missing_block_context = client.post("/api/blocks/context", json={"block_id": "missing-block-id"})
    assert missing_block_context.status_code == 400
    assert "功能块不存在" in missing_block_context.json()["detail"]

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

    diff = client.get(f"/api/projects/{state['current_project_id']}/diff")
    assert diff.status_code == 200
    diff_data = diff.json()
    assert diff_data["from_version"]["version_id"] == state["current_project_version_id"]
    assert diff_data["to_version"]["version_id"] == patched_state["current_project_version_id"]
    assert diff_data["diff"]["summary"]["modified_count"] == 1
    assert diff_data["diff"]["affected_node_ids"] == [node["node_id"]]

    explicit_diff = client.get(
        f"/api/projects/{state['current_project_id']}/diff",
        params={"from_version_id": state["current_project_version_id"], "to_version_id": patched_state["current_project_version_id"]},
    )
    assert explicit_diff.status_code == 200
    assert explicit_diff.json()["diff"] == diff_data["diff"]

    no_parent_diff = client.get(f"/api/projects/{state['current_project_id']}/diff", params={"to_version_id": state["current_project_version_id"]})
    assert no_parent_diff.status_code == 400
    assert "目标版本没有父版本" in no_parent_diff.json()["detail"]

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


def test_api_confirms_or_cancels_medium_risk_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "session_store", FileSessionStore(tmp_path / "sessions"))
    client = TestClient(app)

    created = client.post("/api/sessions", json={"auto_confirm_template": True, "versions_dir": str(tmp_path)})
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]

    message = client.post(
        f"/api/sessions/{thread_id}/message",
        json={"message": "我要做一个风冷热泵机房群控程序，包含水泵控制"},
    )
    assert message.status_code == 200
    state = message.json()["state"]
    original_version_id = state["current_project_version_id"]
    patch = {"op": "disconnect", "target_node_selector": {"id": "3a4c97e"}, "target_input": 0}

    pending = client.post(
        f"/api/sessions/{thread_id}/message",
        json={"message": "断开比较节点的输入", "pending_patch": patch},
    )
    assert pending.status_code == 200
    pending_state = pending.json()["state"]
    assert pending_state["status"] == "awaiting_patch_confirmation"
    assert pending_state["next_action"] == "confirm_patch"
    assert pending_state["current_project_version_id"] == original_version_id
    assert pending_state["pending_confirmation_patch"] == patch
    assert pending_state["planner_dry_run"]["saved"] is False
    assert pending_state["risk_assessment"]["risk_level"] == "medium"

    versions_before_cancel = client.get(f"/api/projects/{state['current_project_id']}/versions")
    assert len(versions_before_cancel.json()["versions"]) == 1

    cancelled = client.post(f"/api/sessions/{thread_id}/patch-confirmation", json={"action": "cancel"})
    assert cancelled.status_code == 200
    cancelled_state = cancelled.json()["state"]
    assert cancelled_state["status"] == "patch_confirmation_cancelled"
    assert cancelled_state["pending_confirmation_patch"] is None
    assert cancelled_state["current_project_version_id"] == original_version_id

    pending_again = client.post(
        f"/api/sessions/{thread_id}/message",
        json={"message": "再次断开比较节点的输入", "pending_patch": patch},
    )
    assert pending_again.status_code == 200
    assert pending_again.json()["state"]["status"] == "awaiting_patch_confirmation"

    approved = client.post(f"/api/sessions/{thread_id}/patch-confirmation", json={"action": "approve"})
    assert approved.status_code == 200
    approved_state = approved.json()["state"]
    assert approved_state["status"] == "patch_applied"
    assert approved_state["validation_report"]["valid"]
    assert approved_state["pending_confirmation_patch"] is None
    assert approved_state["current_project_version_id"] != original_version_id
    assert approved_state["patch_confirmation"]["action"] == "approved"
    assert approved_state["patch_confirmation"]["confirmed_project_version_id"] == original_version_id

    versions_after_approve = client.get(f"/api/projects/{state['current_project_id']}/versions")
    version_items = versions_after_approve.json()["versions"]
    assert len(version_items) == 2
    assert version_items[1]["parent_version_id"] == original_version_id


def test_export_rejects_invalid_project_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_store = FileSessionStore(tmp_path / "sessions")
    monkeypatch.setattr(app.state, "session_store", session_store)
    client = TestClient(app)

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(
        '[{"id":"target","type":"compare","inputs":1,"outputs":1,"wires":[["missing-source"]]}]\n',
        encoding="utf-8",
    )

    export_by_path = client.get("/api/projects/export", params={"path": str(invalid_path)})
    assert export_by_path.status_code == 400
    path_detail = export_by_path.json()["detail"]
    assert path_detail["message"] == "工程校验未通过，拒绝导出。"
    assert path_detail["validation_report"]["exportable"] is False
    assert path_detail["validation_report"]["blocked_export_reasons"] == ["存在 error 级校验问题。"]
    assert path_detail["validation_report"]["issues"][0]["code"] == "missing_wire_source"

    session_store.save(
        "invalid-export-thread",
        {
            "messages": [],
            "project_type": "plant_room",
            "requirement_summary": {},
            "template_candidates": [],
            "selected_template_id": None,
            "current_project_id": "invalid_export_project",
            "current_project_version_id": "v_invalid",
            "current_project_path": str(invalid_path),
            "pending_patch": None,
            "planner_result": None,
            "patch_result": None,
            "validation_report": None,
            "status": "project_version_ready",
            "next_action": None,
            "error": None,
            "auto_confirm_template": True,
            "project_created_in_current_run": False,
            "versions_dir": str(tmp_path),
        },
    )
    export_by_project = client.get("/api/projects/invalid_export_project/export")
    assert export_by_project.status_code == 400
    assert export_by_project.json()["detail"]["validation_report"]["issues"][0]["code"] == "missing_wire_source"
