from __future__ import annotations

from typing import Any

from app.services.advisory import build_advisory_context
from app.services.advisory_kb.context import build_advisory_kb_context
from app.services.advisory_kb.retrievers import build_query_plan


def test_query_plan_classifies_parameter_recommendation() -> None:
    plan = build_query_plan(
        "CO2 阈值多少合适？",
        project_type="ahu",
        intent_result={"topic": "CO2 阈值多少合适？", "should_load_project_context": False},
    )

    assert plan.question_type == "parameter_recommendation"
    assert plan.project_type == "ahu"
    assert plan.function_type == "co2_control"
    assert plan.target_parameter == "CO2"
    assert plan.requires_parameter_stats is True
    assert {"air_quality_logic", "actuator_logic"} <= set(plan.risk_tags)


def test_advisory_kb_context_retrieves_domain_code_and_parameter_stats() -> None:
    context = build_advisory_kb_context(
        "CO2 阈值多少合适？",
        state={"project_type": "ahu"},
        intent_result={"intent": "advisory_intent", "topic": "CO2 阈值多少合适？", "should_load_project_context": False},
    )
    units = context["retrieved_units"]

    assert context["query_plan"]["function_type"] == "co2_control"
    assert context["fusion_summary"]["source_counts"]["domain_kb"] >= 1
    assert context["fusion_summary"]["source_counts"]["code_kb"] >= 1
    assert context["fusion_summary"]["source_counts"]["parameter_stat"] >= 1
    assert any(unit["source_type"] == "domain_kb" and unit["ref"] == "ahu.co2_control.v1" for unit in units)
    assert any(unit["source_type"] == "code_kb" and unit.get("unit_type") in {"subflow", "control_chain"} for unit in units)


def test_high_risk_protection_question_retrieves_rules_and_protection_chains() -> None:
    context = build_advisory_kb_context(
        "防冻保护能不能删掉？",
        state={"project_type": "ahu"},
        intent_result={"intent": "advisory_intent", "topic": "防冻保护能不能删掉？", "should_load_project_context": False},
    )

    assert context["query_plan"]["question_type"] == "risk_review"
    assert "protection_logic" in context["query_plan"]["risk_tags"]
    assert context["risk_rules"]
    assert any(unit["source_type"] == "domain_kb" and unit["ref"] == "ahu.freeze_protection.v1" for unit in context["retrieved_units"])
    assert any(unit["source_type"] == "rule" for unit in context["retrieved_units"])


def test_device_count_question_prioritizes_configuration_model() -> None:
    context = build_advisory_kb_context(
        "2 台热泵改 4 台会影响哪里？",
        state={"project_type": "plant_room"},
        intent_result={"intent": "advisory_intent", "topic": "2 台热泵改 4 台会影响哪里？", "should_load_project_context": False},
    )

    assert context["query_plan"]["function_type"] == "configuration_model"
    assert "device_count_topology" in context["query_plan"]["risk_tags"]
    assert any(unit.get("unit_type") == "configuration_model" and unit["title"] == "使用配置" for unit in context["retrieved_units"])
    assert any("设备轮询启停" in unit["summary"] for unit in context["retrieved_units"] if unit.get("unit_type") == "configuration_model")


def test_build_advisory_context_includes_advisory_kb_without_removing_legacy_context() -> None:
    context = build_advisory_context(
        "旁通压差设定多少合适？",
        state={"project_type": "plant_room"},
        intent_result={
            "intent": "advisory_intent",
            "topic": "旁通压差设定多少合适？",
            "should_search_domain_knowledge": True,
            "should_load_project_context": False,
        },
    )

    assert context["knowledge_context"]
    assert context["patch_support_context"] == []
    assert "advisory_kb_context" in context
    assert context["advisory_kb_context"]["query_plan"]["function_type"] == "bypass_valve_control"
    assert context["advisory_kb_context"]["retrieved_units"]


def test_current_project_refs_are_preserved_in_advisory_kb_context() -> None:
    project_context: dict[str, Any] = {
        "target_resolution": {
            "status": "unique",
            "selected": {
                "display_name": "送风温度设定值",
                "selector": {"id": "node_sat_sp"},
            },
        },
        "tabs": [{"tab_id": "tab_control", "tab_label": "控制"}],
    }

    context = build_advisory_kb_context(
        "送风温度设定值多少合适？",
        state={"project_type": "ahu"},
        intent_result={"intent": "advisory_intent", "topic": "送风温度设定值多少合适？"},
        project_context=project_context,
    )

    assert context["current_project_refs"][0]["id"] == "node_sat_sp"
    assert context["retrieved_units"][0]["source_type"] == "current_project"
