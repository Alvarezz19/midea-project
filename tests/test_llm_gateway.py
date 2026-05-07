from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.services.llm_gateway as llm_gateway
from app.core.config import Settings
from app.main import app
from app.services.json_project import create_project_version
from app.services.advisory import answer_advisory_question, build_advisory_context
from app.services.llm_planner import LLMPlannerError, plan_patch_with_llm
from app.services.planner_execution import plan_patch_with_llm_dry_run_feedback
from app.services.requirement_extractor import extract_requirement_with_llm


PLANT_TEMPLATE = "programs/机房群控程序/风冷热泵标准控制程序[风冷涡旋]20240905.json"
AHU_TEMPLATE = "programs/AHU程序/泰安宁阳中医院/flows_20260206160555.json"


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


def test_advisory_repairs_llm_execution_confusion_without_overwriting_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_prompt: dict[str, str] = {}

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None, **kwargs: Any) -> dict[str, Any]:
        del provider, kwargs
        captured_prompt["system"] = messages[0]["content"]
        captured_prompt["user"] = messages[1]["content"]
        return {
            "status": "needs_more_info",
            "answer": "典型范围可按 22-26°C 讨论，但当前缺少目标节点位置。",
            "topic": "送风温度设定值",
            "recommendation": {"value": None, "unit": "°C", "range": "22-26°C", "confidence": "low"},
            "basis": [{"source": "knowledge/AHU控制策略.md", "summary": "送风温度控制以设定温度为目标。"}],
            "assumptions": ["常规 AHU"],
            "risks": ["设定过低会增加能耗。"],
            "missing_info": ["当前工程中送风温度设定节点的具体位置。"],
            "candidate_requirements": [],
            "adoptable_patch_intent": {
                "executable": False,
                "message": "",
                "reason": "缺少目标节点，不能生成补丁。",
            },
            "_llm_meta": {"provider": "deepseek", "model": "fake", "prompt_name": "advisory_chat"},
        }

    monkeypatch.setattr("app.services.advisory.chat_json", fake_chat_json)
    result = answer_advisory_question(
        "把送风温度设定值改为多少比较好？",
        state={"project_type": "ahu"},
        intent_result={
            "intent": "advisory_intent",
            "topic": "送风温度设定值",
            "should_search_domain_knowledge": True,
            "should_load_project_context": False,
        },
        provider="deepseek",
    )

    assert "咨询是否可回答" in captured_prompt["system"]
    assert "patch_support_context" in captured_prompt["user"]
    assert result["status"] == "answered"
    assert "典型范围可按 22-26°C" in result["answer"]
    assert result["basis"][0]["source"] == "knowledge/AHU控制策略.md"
    assert result["adoptable_patch_intent"]["message"] == "把送风温度设定值改为 24°C"
    assert result["llm_meta"]["provider"] == "deepseek"


def test_advisory_context_keeps_patch_recipes_out_of_primary_knowledge() -> None:
    context = build_advisory_context(
        "把送风温度设定值改为多少比较好？",
        state={"project_type": "ahu"},
        intent_result={
            "intent": "advisory_intent",
            "topic": "送风温度设定值",
            "should_search_domain_knowledge": True,
            "should_load_project_context": False,
        },
    )

    primary_sources = [item["source_path"] for item in context["knowledge_context"]]
    patch_sources = [item["source_path"] for item in context["patch_support_context"]]
    assert primary_sources
    assert "knowledge/补丁配方.md" not in primary_sources
    assert patch_sources
    assert set(patch_sources) == {"knowledge/补丁配方.md"}


@pytest.mark.skipif(os.getenv("RUN_LLM_INTEGRATION") != "1", reason="需要显式开启真实 LLM 集成验收。")
def test_advisory_real_deepseek_answers_typical_design_question() -> None:
    result = answer_advisory_question(
        "把送风温度设定值改为多少比较好？",
        state={"project_type": "ahu"},
        intent_result={
            "intent": "advisory_intent",
            "topic": "送风温度设定值",
            "should_search_domain_knowledge": True,
            "should_load_project_context": False,
        },
        provider="deepseek",
    )

    assert result["status"] == "answered"
    assert result["answer"]
    assert result["basis"]
    assert result["adoptable_patch_intent"]["message"]
    assert result["llm_meta"]["provider"] == "deepseek"


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


def test_llm_planner_uses_context_aware_project_semantics(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_context_project", version_id="v_context", versions_dir=tmp_path)
    captured_prompt: dict[str, Any] = {}
    target_resolution = {
        "status": "resolved",
        "intent": "rename_node",
        "selected": {
            "kind": "node",
            "selector": {"id": "3a4c97e"},
            "display_name": "水泵控制 / 比较判断 / 比较判断 (Compare)",
            "description": "tripPoint=0",
            "confidence": 0.92,
        },
        "candidates": [],
        "questions": [],
    }
    conversation_context = {
        "conversation_summary": {"current_goal": "修改水泵控制"},
        "recent_user_intents": [{"message": "把水泵控制里的比较判断改名"}],
        "last_affected_node_ids": ["3a4c97e"],
        "last_touched_entities": [{"display_name": "水泵控制 / 比较判断"}],
    }

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        del provider
        captured_prompt["system"] = messages[0]["content"]
        captured_prompt["user"] = messages[1]["content"]
        assert "禁止要求用户提供节点 ID" in messages[0]["content"]
        assert "target_resolution" in messages[1]["content"]
        assert "current_project_nodes" in messages[1]["content"]
        assert "node_neighborhoods" in messages[1]["content"]
        assert "last_affected_node_ids" in messages[1]["content"]
        assert "3a4c97e" in messages[1]["content"]
        return {
            "status": "planned",
            "intent": "modify_existing_logic",
            "summary": "按语义定位结果改名。",
            "risk_level": "low",
            "risk_reasons": [],
            "required_context": [],
            "operations": [{"op": "rename_node", "node_selector": {"id": "3a4c97e"}, "new_name": "上下文LLM-比较节点"}],
            "validation_expectations": ["目标节点唯一"],
            "questions": [],
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)

    result = plan_patch_with_llm(
        "把水泵控制里的比较判断改名为 上下文LLM-比较节点",
        project_path=metadata["version_path"],
        template_id="plant_room_efb00c114dcb",
        project_type="plant_room",
        conversation_context=conversation_context,
        target_resolution=target_resolution,
    )

    assert result["status"] == "planned"
    assert result["context"]["target_resolution"] == target_resolution
    assert result["context"]["conversation_context"] == conversation_context
    assert result["context"]["current_project_nodes"][0]["node_id"] == "3a4c97e"
    assert result["context"]["node_neighborhoods"]
    assert result["pending_patch"]["operations"][0]["node_selector"] == {"id": "3a4c97e"}
    assert captured_prompt["user"]


def test_llm_planner_uses_locator_knowledge_queries(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_knowledge_project", version_id="v_knowledge", versions_dir=tmp_path)
    searched_queries: list[str] = []
    target_resolution = {
        "status": "needs_clarification",
        "intent": "connect",
        "selected": None,
        "candidates": [],
        "questions": [],
        "knowledge_queries": ["旁通阀压差设定接入 补丁 配方"],
    }

    def fake_search_knowledge(query: str, *, limit: int = 5, **kwargs: Any) -> list[dict[str, Any]]:
        del limit, kwargs
        searched_queries.append(query)
        return [
            {
                "chunk_id": f"chunk-{len(searched_queries)}",
                "source_path": "knowledge/补丁配方.md",
                "title": "旁通阀压差设定接入",
                "heading_level": 2,
                "content": f"检索词：{query}",
                "score": 1.0,
            }
        ]

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        del provider
        assert "旁通阀压差设定接入 补丁 配方" in messages[1]["content"]
        assert "机房 旁通阀 压差设定 新增 接入 比较判断 补丁配方" in messages[1]["content"]
        assert "context_used" in messages[1]["content"]
        return {
            "status": "needs_clarification",
            "intent": "unknown",
            "summary": "需要确认接线位置。",
            "risk_level": "medium",
            "risk_reasons": ["新增节点并接线需要确认"],
            "required_context": [],
            "operations": [],
            "validation_expectations": [],
            "questions": ["请确认接入比较判断的动态阈值输入，还是只新增设定节点。"],
        }

    monkeypatch.setattr("app.services.llm_planner.search_knowledge", fake_search_knowledge)
    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)

    result = plan_patch_with_llm(
        "新增旁通阀压差设定",
        project_path=metadata["version_path"],
        template_id="plant_room_efb00c114dcb",
        project_type="plant_room",
        target_resolution=target_resolution,
    )

    assert searched_queries[0] == "旁通阀压差设定接入 补丁 配方"
    assert "新增旁通阀压差设定" in searched_queries
    assert any("旁通阀 压差设定" in query and "补丁配方" in query for query in searched_queries)
    assert result["status"] == "needs_clarification"
    assert result["context"]["context_used"]["knowledge_count"] >= 3


@pytest.mark.parametrize(
    ("template_path", "project_type", "message", "expected_title"),
    [
        (AHU_TEMPLATE, "ahu", "新增 CO2 浓度设定并接入新风阀控制", "AHU CO2 浓度设定接入新风阀控制"),
        (AHU_TEMPLATE, "ahu", "送风 PID 输出上限改成动态设定并接入常量", "PID 输出上限和下限动态输入"),
        (PLANT_TEMPLATE, "plant_room", "新增旁通阀压差设定并接入比较判断", "机房旁通阀压差设定新增并接入比较判断"),
        (PLANT_TEMPLATE, "plant_room", "水泵运行台数阈值改为 3", "机房水泵运行台数阈值修改"),
        (PLANT_TEMPLATE, "plant_room", "修改 IO 点位通道为扩展模块 1 通道 31", "IO 和通讯点位修改风险"),
    ],
)
def test_llm_planner_retrieves_patch_recipe_for_complex_intents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    template_path: str,
    project_type: str,
    message: str,
    expected_title: str,
) -> None:
    version_id = "v_recipe_" + str(abs(hash(message)) % 100000)
    metadata = create_project_version(template_path, project_id=f"recipe_{project_type}", version_id=version_id, versions_dir=tmp_path)

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        del provider
        assert expected_title in messages[1]["content"]
        return {
            "status": "needs_clarification",
            "intent": "unknown",
            "summary": "需要确认业务细节。",
            "risk_level": "medium",
            "risk_reasons": ["复杂补丁需要确认。"],
            "required_context": [],
            "operations": [],
            "validation_expectations": [],
            "questions": ["请确认目标对象和接入位置。"],
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)

    result = plan_patch_with_llm(
        message,
        project_path=metadata["version_path"],
        project_type=project_type,
    )

    assert result["status"] == "needs_clarification"
    assert any(
        item["source_path"] == "knowledge/补丁配方.md" and expected_title in item["title"]
        for item in result["context"]["knowledge"]
    )


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


def test_llm_planner_accepts_add_tab_operation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_add_tab", versions_dir=tmp_path)

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        assert "add_tab" in messages[0]["content"]
        return {
            "status": "planned",
            "intent": "add_logic",
            "summary": "新增 CO2 控制页面。",
            "risk_level": "low",
            "risk_reasons": [],
            "required_context": [{"type": "tab", "query": "CO2 控制", "reason": "确认页面不存在"}],
            "operations": [{"op": "add_tab", "label": "CO2 控制", "info": "新增 CO2 控制逻辑页面"}],
            "validation_expectations": ["页面标签不重复", "dry-run 校验通过"],
            "questions": [],
            "_llm_meta": {"provider": provider or "deepseek", "model": "fake"},
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    result = plan_patch_with_llm("新增一个 CO2 控制页面", project_path=metadata["version_path"])

    assert result["status"] == "planned"
    assert result["risk_level"] == "low"
    assert result["pending_patch"] == {
        "operations": [{"op": "add_tab", "label": "CO2 控制", "info": "新增 CO2 控制逻辑页面"}]
    }


def test_llm_planner_accepts_copy_block_operation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_copy_block", versions_dir=tmp_path)

    def fake_chat_json(messages: list[dict[str, str]], *, provider: str | None = None) -> dict[str, Any]:
        assert "copy_block" in messages[0]["content"]
        return {
            "status": "planned",
            "intent": "add_logic",
            "summary": "复制旁通阀控制功能块。",
            "risk_level": "high",
            "risk_reasons": ["复制局部子图，需要人工确认入口出口。"],
            "required_context": [{"type": "block", "query": "旁通阀控制", "reason": "定位源功能块"}],
            "operations": [
                {
                    "op": "copy_block",
                    "block_id": "block_3f996bfafceb",
                    "target_tab_selector": {"label": "旁通阀控制"},
                    "x_offset": 80,
                    "y_offset": 80,
                    "name_prefix": "复制-",
                }
            ],
            "validation_expectations": ["重写节点 id", "只保留内部连线", "不自动接外部线"],
            "questions": [],
        }

    monkeypatch.setattr("app.services.llm_planner.chat_json", fake_chat_json)
    result = plan_patch_with_llm("复制旁通阀控制功能块", project_path=metadata["version_path"])

    assert result["status"] == "planned"
    assert result["risk_level"] == "high"
    assert result["pending_patch"]["operations"][0]["op"] == "copy_block"
    assert result["pending_patch"]["operations"][0]["target_tab_selector"] == {"label": "旁通阀控制"}


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


def test_planner_dry_run_api_falls_back_to_llm_for_structural_intent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    metadata = create_project_version(PLANT_TEMPLATE, project_id="llm_planner_project", version_id="v_api_fallback", versions_dir=tmp_path)
    pending_patch = {"op": "replace_constant", "node_selector": {"id": "67febfa"}, "field": "fixedValue", "value": "8"}

    def fake_llm_dry_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
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

    monkeypatch.setattr("app.main.plan_patch_with_llm_dry_run_feedback", fake_llm_dry_run)
    client = TestClient(app)

    response = client.post(
        "/api/planner/dry-run",
        json={
            "message": "新增旁通阀压差设定",
            "project_path": metadata["version_path"],
            "template_id": "plant_room_efb00c114dcb",
            "project_type": "plant_room",
            "use_llm": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run_valid"
    assert data["planner_result"]["planner"] == "llm"
    assert data["planner_result"]["fallback_from"] == "rule"
    assert data["planner_result"]["rule_planner_result"]["status"] == "needs_clarification"
    assert data["pending_patch"] == pending_patch


def test_llm_dry_run_feedback_normalizes_const_input_param_aliases() -> None:
    captured_patch: dict[str, Any] = {}

    def fake_planner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {
            "status": "planned",
            "planner": "llm",
            "risk_level": "medium",
            "pending_patch": {
                "operations": [
                    {
                        "op": "add_node_from_schema",
                        "module_type": "constInput",
                        "tab_selector": {"label": "旁通阀控制"},
                        "params": {"name": "评测旁通阀压差设定", "value": 45},
                    },
                    {"op": "enable_dynamic_input", "node_selector": {"id": "169a370"}},
                ]
            },
            "questions": [],
        }

    def fake_dry_runner(project_path: str, pending_patch: dict[str, Any]) -> dict[str, Any]:
        del project_path
        captured_patch.update(pending_patch)
        return {
            "saved": False,
            "valid": True,
            "validation_report": {"valid": True, "issues": []},
            "diff": {"summary": {"added_count": 1, "modified_count": 1, "affected_node_count": 2}},
            "nodes": [{"id": "should_be_removed"}],
        }

    result = plan_patch_with_llm_dry_run_feedback(
        "新增旁通阀压差设定",
        project_path=PLANT_TEMPLATE,
        planner=fake_planner,
        dry_runner=fake_dry_runner,
    )

    add_node = captured_patch["operations"][0]
    assert add_node["params"] == {"user_defined_name": "评测旁通阀压差设定", "fixedValue": 45}
    assert result["pending_patch"]["operations"][0]["params"] == add_node["params"]
    assert result["dry_run"]["valid"] is True
    assert "nodes" not in result["dry_run"]


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
