from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.services.llm_gateway as llm_gateway
from app.core.config import Settings
from app.main import app
from app.services.json_project import create_project_version
from app.services.llm_planner import LLMPlannerError, plan_patch_with_llm
from app.services.requirement_extractor import extract_requirement_with_llm


PLANT_TEMPLATE = "programs/机房群控程序/风冷热泵标准控制程序[风冷涡旋]20240905.json"


def test_llm_gateway_parses_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post_json(url: str, payload: dict[str, Any], *, api_key: str, provider: str, timeout: float) -> dict[str, Any]:
        captured.update({"url": url, "payload": payload, "api_key": api_key, "provider": provider, "timeout": timeout})
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"project_type":"ahu","summary":"测试","missing_fields":[]}'
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(llm_gateway, "_post_json", fake_post_json)
    settings = Settings(deepseek_api_key="test-key")
    result = llm_gateway.chat_json([{"role": "user", "content": "输出 json"}], provider="deepseek", settings=settings)

    assert result["project_type"] == "ahu"
    assert result["_llm_meta"]["provider"] == "deepseek"
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["api_key"]


def test_requirement_extractor_validates_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        assert "json" in messages[0]["content"].lower()
        return {
            "project_type": "plant_room",
            "project_type_confidence": 0.93,
            "summary": "风冷热泵机房群控，包含水泵和旁通阀。",
            "equipment": ["风冷热泵", "水泵", "旁通阀"],
            "control_features": ["水泵控制", "旁通阀压差控制"],
            "communication": ["Modbus"],
            "io_points": [],
            "protection_logic": ["故障反馈"],
            "missing_fields": ["设备数量"],
            "clarification_questions": ["请确认水泵数量。"],
            "risk_level": "low",
            "risk_reasons": [],
            "raw_intent": "创建机房群控程序",
            "_llm_meta": {"provider": provider or "deepseek", "model": "fake"},
        }

    monkeypatch.setattr("app.services.requirement_extractor.chat_json", fake_chat_json)
    result = extract_requirement_with_llm("我要做风冷热泵机房群控，包含水泵和旁通阀，Modbus 通讯。")

    assert result["project_type"] == "plant_room"
    assert result["equipment"] == ["风冷热泵", "水泵", "旁通阀"]
    assert result["clarification_questions"] == ["请确认水泵数量。"]
    assert result["llm_meta"]["provider"] == "deepseek"


def test_requirement_extract_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_extract_requirement(message: str, *, provider: str | None = None) -> dict[str, Any]:
        return {
            "project_type": "ahu",
            "project_type_confidence": 0.9,
            "summary": message,
            "equipment": ["送风机"],
            "control_features": [],
            "communication": [],
            "io_points": [],
            "protection_logic": [],
            "missing_fields": [],
            "clarification_questions": [],
            "risk_level": "low",
            "risk_reasons": [],
            "raw_intent": message,
            "llm_meta": {"provider": provider or "deepseek"},
        }

    monkeypatch.setattr("app.main.extract_requirement_with_llm", fake_extract_requirement)
    client = TestClient(app)

    response = client.post("/api/requirements/extract", json={"message": "我要做 AHU 程序", "provider": "deepseek"})

    assert response.status_code == 200
    assert response.json()["project_type"] == "ahu"
    assert response.json()["llm_meta"]["provider"] == "deepseek"


def test_llm_planner_validates_structured_patch_plan(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v1", versions_dir=tmp_path)

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        assert "结构化补丁计划" in messages[1]["content"]
        return {
            "status": "planned",
            "intent": "add_logic",
            "summary": "新增一个常量并接入水泵比较判断。",
            "risk_level": "medium",
            "risk_reasons": ["新增节点并修改连线"],
            "required_context": [{"type": "block", "query": "水泵控制", "reason": "定位目标页面和比较节点"}],
            "operations": [
                {
                    "op": "add_node_from_schema",
                    "module_type": "constInput",
                    "tab_selector": {"label": "水泵控制"},
                    "params": {"user_defined_name": "LLM测试常量", "fixedValue": 9},
                    "x": 220,
                    "y": 260,
                },
                {
                    "op": "connect",
                    "source_node_selector": {"type": "constInput", "name": "LLM测试常量"},
                    "source_output": 0,
                    "target_node_selector": {"id": "3a4c97e"},
                    "target_input": 1,
                },
            ],
            "validation_expectations": ["新增节点 id 不重复", "目标节点唯一", "dry-run 校验通过"],
            "questions": [],
            "_llm_meta": {"provider": provider or "deepseek", "model": "fake"},
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    result = plan_patch_with_llm(
        "在水泵控制里新增一个常量并接到比较判断",
        project_path=metadata["version_path"],
        template_id="plant_room_efb00c114dcb",
        project_type="plant_room",
    )

    assert result["status"] == "planned"
    assert result["planner"] == "llm"
    assert result["risk_level"] == "medium"
    assert result["pending_patch"]["operations"][0]["op"] == "add_node_from_schema"
    assert result["pending_patch"]["operations"][1]["op"] == "connect"
    assert result["context"]["block_contexts"]
    assert result["llm_meta"]["provider"] == "deepseek"


def test_llm_planner_accepts_replace_constant_operation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_replace", versions_dir=tmp_path)

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        del messages
        return {
            "status": "planned",
            "intent": "modify_existing_logic",
            "summary": "替换常量节点的固定值。",
            "risk_level": "low",
            "risk_reasons": [],
            "required_context": [{"type": "node", "query": "67febfa 常量", "reason": "定位目标常量"}],
            "operations": [{"op": "replace_constant", "node_selector": {"id": "67febfa"}, "field": "fixedValue", "value": "6"}],
            "validation_expectations": ["目标节点唯一", "只修改 fixedValue"],
            "questions": [],
            "_llm_meta": {"provider": provider or "deepseek", "model": "fake"},
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    result = plan_patch_with_llm(
        "把节点 67febfa 的常量值替换为 6",
        project_path=metadata["version_path"],
        template_id="plant_room_efb00c114dcb",
        project_type="plant_room",
    )

    assert result["status"] == "planned"
    assert result["risk_level"] == "low"
    assert result["pending_patch"] == {
        "operations": [{"op": "replace_constant", "node_selector": {"id": "67febfa"}, "field": "fixedValue", "value": "6"}]
    }


def test_llm_planner_accepts_enable_dynamic_input_operation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_dynamic", versions_dir=tmp_path)

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        del messages
        return {
            "status": "planned",
            "intent": "modify_existing_logic",
            "summary": "启用比较节点的动态阈值输入。",
            "risk_level": "medium",
            "risk_reasons": ["修改节点输入端口数量"],
            "required_context": [{"type": "node", "query": "f22a5df 比较判断", "reason": "定位目标节点"}],
            "operations": [{"op": "enable_dynamic_input", "node_selector": {"id": "f22a5df"}}],
            "validation_expectations": ["目标节点唯一", "inputs 和 wires 同步"],
            "questions": [],
            "_llm_meta": {"provider": provider or "deepseek", "model": "fake"},
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    result = plan_patch_with_llm(
        "启用节点 f22a5df 的动态阈值输入",
        project_path=metadata["version_path"],
        template_id="plant_room_efb00c114dcb",
        project_type="plant_room",
    )

    assert result["status"] == "planned"
    assert result["risk_level"] == "medium"
    assert result["pending_patch"] == {"operations": [{"op": "enable_dynamic_input", "node_selector": {"id": "f22a5df"}}]}


def test_llm_planner_rejects_unknown_operations(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_bad", versions_dir=tmp_path)

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        return {
            "status": "planned",
            "intent": "delete_logic",
            "summary": "删除节点。",
            "risk_level": "high",
            "risk_reasons": ["删除逻辑"],
            "required_context": [],
            "operations": [{"op": "delete_node", "node_selector": {"id": "3a4c97e"}}],
            "validation_expectations": [],
            "questions": [],
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    with pytest.raises(LLMPlannerError, match="不符合 schema"):
        plan_patch_with_llm("删除节点 3a4c97e", project_path=metadata["version_path"])


def test_llm_planner_retries_schema_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_retry", versions_dir=tmp_path)
    calls: list[list[dict[str, str]]] = []

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        del provider
        calls.append(messages)
        if len(calls) == 1:
            return {
                "status": "planned",
                "intent": "delete_logic",
                "summary": "错误地输出未知 op。",
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
            "operations": [{"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": "schema重试-比较节点"}],
            "validation_expectations": ["目标节点唯一"],
            "questions": [],
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    result = plan_patch_with_llm("把节点 3a4c97e 改名", project_path=metadata["version_path"], max_attempts=2)

    assert result["status"] == "planned"
    assert result["planner_attempt_count"] == 2
    assert result["pending_patch"]["operations"][0]["op"] == "rename_node"
    assert len(calls) == 2


def test_llm_planner_dry_run_api_executes_structured_plan(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_api", versions_dir=tmp_path)

    def fake_plan_patch_with_llm(
        message: str,
        *,
        project_path: str,
        template_id: str | None = None,
        project_type: str | None = None,
        provider: str | None = None,
        max_attempts: int = 2,
        feedback_messages: list[str] | None = None,
    ) -> dict[str, Any]:
        del max_attempts, feedback_messages
        return {
            "status": "planned",
            "planner": "llm",
            "summary": message,
            "risk_level": "medium",
            "operations": [
                {
                    "op": "add_node_from_schema",
                    "module_type": "constInput",
                    "tab_selector": {"label": "水泵控制"},
                    "params": {"user_defined_name": "API LLM测试常量", "fixedValue": 11},
                },
                {
                    "op": "connect",
                    "source_node_selector": {"type": "constInput", "name": "API LLM测试常量"},
                    "source_output": 0,
                    "target_node_selector": {"id": "3a4c97e"},
                    "target_input": 1,
                },
            ],
            "pending_patch": {
                "operations": [
                    {
                        "op": "add_node_from_schema",
                        "module_type": "constInput",
                        "tab_selector": {"label": "水泵控制"},
                        "params": {"user_defined_name": "API LLM测试常量", "fixedValue": 11},
                    },
                    {
                        "op": "connect",
                        "source_node_selector": {"type": "constInput", "name": "API LLM测试常量"},
                        "source_output": 0,
                        "target_node_selector": {"id": "3a4c97e"},
                        "target_input": 1,
                    },
                ]
            },
            "questions": [],
        }

    monkeypatch.setattr("app.services.planner_execution.plan_patch_with_llm", fake_plan_patch_with_llm)
    client = TestClient(app)

    response = client.post(
        "/api/planner/dry-run",
        json={
            "message": "新增常量并接入水泵比较判断",
            "project_path": metadata["version_path"],
            "template_id": "plant_room_efb00c114dcb",
            "project_type": "plant_room",
            "use_llm": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run_valid"
    assert data["planner_result"]["planner"] == "llm"
    assert data["dry_run"]["saved"] is False
    assert data["dry_run"]["diff"]["summary"]["added_count"] == 1
    assert data["dry_run"]["diff"]["summary"]["modified_count"] == 1
    assert "nodes" not in data["dry_run"]


def test_llm_planner_dry_run_api_retries_patch_engine_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_api_retry", versions_dir=tmp_path)
    calls: list[list[str] | None] = []

    def fake_plan_patch_with_llm(
        message: str,
        *,
        project_path: str,
        template_id: str | None = None,
        project_type: str | None = None,
        provider: str | None = None,
        max_attempts: int = 2,
        feedback_messages: list[str] | None = None,
    ) -> dict[str, Any]:
        del message, project_path, template_id, project_type, provider, max_attempts
        calls.append(feedback_messages)
        if len(calls) == 1:
            return {
                "status": "planned",
                "planner": "llm",
                "risk_level": "low",
                "pending_patch": {"op": "rename_node", "node_selector": {"type": "compare"}, "new_name": "不应执行"},
                "questions": [],
            }
        assert feedback_messages
        assert "dry-run 执行失败" in feedback_messages[-1]
        return {
            "status": "planned",
            "planner": "llm",
            "risk_level": "low",
            "pending_patch": {"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": "API重试-比较节点"},
            "questions": [],
        }

    monkeypatch.setattr("app.services.planner_execution.plan_patch_with_llm", fake_plan_patch_with_llm)
    client = TestClient(app)

    response = client.post(
        "/api/planner/dry-run",
        json={
            "message": "把比较判断改名",
            "project_path": metadata["version_path"],
            "template_id": "plant_room_efb00c114dcb",
            "project_type": "plant_room",
            "use_llm": True,
            "llm_max_attempts": 2,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run_valid"
    assert data["pending_patch"]["node_selector"] == {"id": "3a4c97e"}
    assert len(data["planner_attempts"]) == 2
    assert "dry_run_error" in data["planner_attempts"][0]
    assert len(calls) == 2


def test_llm_planner_dry_run_api_retries_validation_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_api_validation_retry", versions_dir=tmp_path)
    planner_feedback: list[list[str] | None] = []
    dry_run_calls: list[dict[str, Any]] = []

    def fake_plan_patch_with_llm(
        message: str,
        *,
        project_path: str,
        template_id: str | None = None,
        project_type: str | None = None,
        provider: str | None = None,
        max_attempts: int = 2,
        feedback_messages: list[str] | None = None,
    ) -> dict[str, Any]:
        del message, project_path, template_id, project_type, provider, max_attempts
        planner_feedback.append(feedback_messages)
        suffix = len(planner_feedback)
        return {
            "status": "planned",
            "planner": "llm",
            "risk_level": "low",
            "pending_patch": {"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": f"校验重试-{suffix}"},
            "questions": [],
        }

    def fake_dry_run_patch_to_project(project_path: str, pending_patch: dict[str, Any]) -> dict[str, Any]:
        del project_path
        dry_run_calls.append(pending_patch)
        if len(dry_run_calls) == 1:
            return {
                "saved": False,
                "valid": False,
                "validation_report": {
                    "issues": [{"code": "missing_wire_source", "message": "缺少上游源引用。", "node_id": "3a4c97e"}]
                },
                "diff": {"summary": {"modified_count": 1}},
                "nodes": [],
            }
        return {
            "saved": False,
            "valid": True,
            "validation_report": {"valid": True, "issues": []},
            "diff": {"summary": {"modified_count": 1}},
            "nodes": [],
        }

    monkeypatch.setattr("app.services.planner_execution.plan_patch_with_llm", fake_plan_patch_with_llm)
    monkeypatch.setattr("app.services.planner_execution.dry_run_patch_to_project", fake_dry_run_patch_to_project)
    client = TestClient(app)

    response = client.post(
        "/api/planner/dry-run",
        json={
            "message": "把节点改名并触发校验失败重试",
            "project_path": metadata["version_path"],
            "use_llm": True,
            "llm_max_attempts": 2,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run_valid"
    assert len(dry_run_calls) == 2
    assert "dry-run 校验未通过" in planner_feedback[1][-1]
    assert data["planner_attempts"][0]["validation_error"].startswith("missing_wire_source")


@pytest.mark.skipif(os.getenv("RUN_LLM_INTEGRATION") != "1", reason="需要显式开启真实 LLM 集成验收。")
def test_requirement_extractor_real_deepseek() -> None:
    result = extract_requirement_with_llm(
        "我要做一个 AHU 程序，包含送风机、排风机、直膨机和 Modbus 通讯，需要保留故障报警。",
        provider="deepseek",
    )

    assert result["project_type"] == "ahu"
    assert result["project_type_confidence"] >= 0.5
    assert any("排风" in item for item in result["equipment"] + result["control_features"])
    assert result["llm_meta"]["provider"] == "deepseek"
    assert result["llm_meta"]["usage"] is None or result["llm_meta"]["usage"].get("total_tokens", 0) > 0
