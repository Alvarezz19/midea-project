from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.advisory_kb.loader import AdvisoryKbLoadError, load_index
from app.services.advisory_kb.schema import DomainKnowledgeCard, load_domain_cards
from app.services.retrieval import text_score, tokenize


QUESTION_TYPES = {
    "parameter_recommendation",
    "strategy_review",
    "risk_review",
    "impact_analysis",
    "commissioning",
    "patchability",
}


@dataclass(frozen=True)
class AdvisoryQueryPlan:
    question_type: str
    project_type: str | None
    equipment: list[str]
    function_type: str
    target_parameter: str | None
    requires_current_project: bool
    requires_parameter_stats: bool
    risk_tags: list[str]
    query_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_type": self.question_type,
            "project_type": self.project_type,
            "equipment": self.equipment,
            "function_type": self.function_type,
            "target_parameter": self.target_parameter,
            "requires_current_project": self.requires_current_project,
            "requires_parameter_stats": self.requires_parameter_stats,
            "risk_tags": self.risk_tags,
            "query_terms": self.query_terms,
        }


def build_query_plan(
    message: str,
    *,
    project_type: Any = None,
    intent_result: dict[str, Any] | None = None,
) -> AdvisoryQueryPlan:
    text = message.strip()
    inferred_project_type = _infer_project_type(text, project_type)
    function_type = _infer_function_type(text, inferred_project_type)
    question_type = _infer_question_type(text)
    risk_tags = _infer_risk_tags(text, function_type)
    equipment = _infer_equipment(text, function_type, inferred_project_type)
    target_parameter = _infer_target_parameter(text)
    related_entities = []
    if isinstance(intent_result, dict):
        related_entities = [str(item) for item in intent_result.get("related_entities") or [] if str(item).strip()]
    query_terms = _unique_terms([text, str((intent_result or {}).get("topic") or ""), function_type, target_parameter or "", *equipment, *risk_tags, *related_entities])
    return AdvisoryQueryPlan(
        question_type=question_type,
        project_type=inferred_project_type,
        equipment=equipment,
        function_type=function_type,
        target_parameter=target_parameter,
        requires_current_project=question_type in {"impact_analysis", "risk_review", "patchability"} or bool((intent_result or {}).get("should_load_project_context", True)),
        requires_parameter_stats=question_type == "parameter_recommendation",
        risk_tags=risk_tags,
        query_terms=query_terms,
    )


def retrieve_domain_cards(plan: AdvisoryQueryPlan, *, limit: int = 5) -> list[dict[str, Any]]:
    cards = load_domain_cards()
    results: list[dict[str, Any]] = []
    query = " ".join(plan.query_terms)
    query_tokens = tokenize(query)
    for card in cards:
        metadata = card.metadata
        if metadata.get("project_type") not in {plan.project_type, "common"}:
            continue
        score = _score_domain_card(card, plan, query_tokens)
        if score <= 0:
            continue
        results.append(
            {
                "evidence_id": str(metadata.get("id")),
                "source_type": "domain_kb",
                "source_path": card.relative_path,
                "ref": str(metadata.get("id")),
                "title": str(metadata.get("title") or ""),
                "summary": _section_summary(card, "结论"),
                "score": round(score, 4),
                "risk_tags": list(metadata.get("risk_tags") or []),
                "confidence": "medium" if metadata.get("review_status") == "generated" else "high",
                "review_status": metadata.get("review_status"),
                "function_type": metadata.get("function_type"),
                "patchability": metadata.get("patchability"),
                "missing_info": card.sections.get("需要追问的信息", ""),
            }
        )
    results.sort(key=lambda item: float(item["score"]), reverse=True)
    return results[:limit]


def retrieve_code_units(plan: AdvisoryQueryPlan, *, limit: int = 8) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for index_name, unit_type in [
        ("template_profile", "template"),
        ("tab_role", "tab"),
        ("point_identity", "point"),
        ("quote_reference", "quote_reference"),
        ("point_usage", "point_usage"),
        ("subflow", "subflow"),
        ("configuration_model", "configuration_model"),
        ("bitmask_mapping", "bitmask_mapping"),
        ("algorithm_role", "algorithm_role"),
    ]:
        for row in _safe_load_index(index_name):
            if plan.project_type and row.get("project_type") != plan.project_type:
                continue
            score = _score_code_row(row, plan)
            if score <= 0:
                continue
            units.append(_code_evidence(row, index_name=index_name, unit_type=unit_type, score=score))
    units.sort(key=lambda item: float(item["score"]), reverse=True)
    return units[:limit]


def retrieve_control_chains(plan: AdvisoryQueryPlan, *, limit: int = 4) -> list[dict[str, Any]]:
    return _retrieve_chain_index("control_chain", plan, limit=limit, source_type="code_kb")


def retrieve_protection_chains(plan: AdvisoryQueryPlan, *, limit: int = 4) -> list[dict[str, Any]]:
    chains = _retrieve_chain_index("protection_chain", plan, limit=limit, source_type="rule")
    if plan.risk_tags and "protection_logic" in plan.risk_tags:
        chains = sorted(chains, key=lambda item: float(item.get("score") or 0) + 3.0, reverse=True)
    return chains[:limit]


def retrieve_parameter_stats(plan: AdvisoryQueryPlan, *, limit: int = 4) -> list[dict[str, Any]]:
    if not plan.requires_parameter_stats:
        return []
    results: list[dict[str, Any]] = []
    query = " ".join(plan.query_terms)
    query_tokens = tokenize(query)
    for row in _safe_load_index("parameter_stat"):
        if plan.project_type and row.get("project_type") != plan.project_type:
            continue
        text = f"{row.get('function_type', '')} {row.get('parameter_name', '')} {row.get('unit', '')}"
        score = text_score(text, query_tokens)
        if row.get("function_type") == plan.function_type:
            score += 2.0
        if plan.target_parameter and plan.target_parameter in str(row.get("parameter_name") or ""):
            score += 2.0
        if score <= 0:
            continue
        results.append(
            {
                "evidence_id": str(row.get("stat_id")),
                "source_type": "parameter_stat",
                "source_path": str(row.get("source_path") or "indexes/advisory/parameter_stat_index.jsonl"),
                "ref": str(row.get("stat_id")),
                "title": str(row.get("parameter_name") or ""),
                "summary": f"历史样本 {row.get('count')} 个，中位数 {row.get('median')}，范围 {row.get('min')}~{row.get('max')} {row.get('unit') or ''}。仅作样例统计。",
                "score": round(score, 4),
                "risk_tags": [],
                "confidence": "low",
                "function_type": row.get("function_type"),
                "stat": {
                    "count": row.get("count"),
                    "min": row.get("min"),
                    "max": row.get("max"),
                    "median": row.get("median"),
                    "unit": row.get("unit"),
                },
            }
        )
    results.sort(key=lambda item: float(item["score"]), reverse=True)
    return results[:limit]


def retrieve_risk_rules(plan: AdvisoryQueryPlan, *, limit: int = 4) -> list[dict[str, Any]]:
    risk_cards = [
        item
        for item in retrieve_domain_cards(plan, limit=12)
        if "risk" in str(item.get("function_type") or "") or set(item.get("risk_tags") or []) & set(plan.risk_tags)
    ]
    protection_chains = retrieve_protection_chains(plan, limit=limit)
    combined = risk_cards + protection_chains
    combined.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return combined[:limit]


def current_project_refs_from_project_context(project_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(project_context, dict) or not project_context:
        return []
    refs: list[dict[str, Any]] = []
    target_resolution = project_context.get("target_resolution") if isinstance(project_context.get("target_resolution"), dict) else {}
    selected = target_resolution.get("selected") if isinstance(target_resolution.get("selected"), dict) else None
    if selected:
        selector = selected.get("selector") if isinstance(selected.get("selector"), dict) else {}
        refs.append(
            {
                "kind": "node",
                "id": selector.get("id"),
                "display_name": selected.get("display_name") or selected.get("description") or selector.get("id"),
                "selector": selector,
                "source": "semantic_locator",
            }
        )
    for tab in project_context.get("tabs") or []:
        if isinstance(tab, dict):
            refs.append(
                {
                    "kind": "tab",
                    "id": tab.get("tab_id") or tab.get("id"),
                    "display_name": tab.get("tab_label") or tab.get("label"),
                    "selector": {"id": tab.get("tab_id") or tab.get("id")},
                    "source": "current_project_summary",
                }
            )
    return [item for item in refs if item.get("id") or item.get("display_name")][:8]


def _score_domain_card(card: DomainKnowledgeCard, plan: AdvisoryQueryPlan, query_tokens: list[str]) -> float:
    metadata = card.metadata
    text = " ".join(
        [
            str(metadata.get("title") or ""),
            str(metadata.get("function_type") or ""),
            " ".join(str(item) for item in metadata.get("equipment") or []),
            " ".join(str(item) for item in metadata.get("question_patterns") or []),
            " ".join(str(item) for item in metadata.get("risk_tags") or []),
            card.body,
        ]
    )
    score = text_score(text, query_tokens)
    if metadata.get("function_type") == plan.function_type:
        score += 3.0
    if set(metadata.get("risk_tags") or []) & set(plan.risk_tags):
        score += 1.5
    if plan.question_type == metadata.get("answer_type"):
        score += 1.0
    if metadata.get("review_status") == "reviewed":
        score += 0.8
    elif metadata.get("review_status") == "generated":
        score += 0.2
    return score


def _score_code_row(row: dict[str, Any], plan: AdvisoryQueryPlan) -> float:
    query = " ".join(plan.query_terms)
    text = " ".join(
        str(value)
        for value in [
            row.get("title"),
            row.get("summary"),
            row.get("file_name"),
            row.get("tab_label"),
            row.get("tab_role"),
            row.get("point_name"),
            row.get("referenced_display"),
            row.get("function_type"),
            row.get("chain_type"),
            row.get("algorithm_role"),
            row.get("text_for_search"),
            row.get("template_archetype"),
            row.get("io_binding_level"),
            _json_text(row.get("config_points")),
            _json_text(row.get("affects")),
            _json_text(row.get("configurability")),
            _json_text(row.get("tabs")),
        ]
        if value
    )
    score = text_score(text, tokenize(query))
    if row.get("function_type") == plan.function_type:
        score += 2.0
    if row.get("tab_role") in _expected_tab_roles(plan.function_type):
        score += 1.0
    if set(row.get("risk_tags") or []) & set(plan.risk_tags):
        score += 1.2
    if plan.function_type == "configuration_model" and row.get("tab_role") == "configuration":
        score += 2.0
    return score


def _retrieve_chain_index(index_name: str, plan: AdvisoryQueryPlan, *, limit: int, source_type: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in _safe_load_index(index_name):
        if plan.project_type and row.get("project_type") != plan.project_type:
            continue
        score = _score_code_row(row, plan)
        if score <= 0:
            continue
        results.append(_code_evidence(row, index_name=index_name, unit_type=index_name, score=score, source_type=source_type))
    results.sort(key=lambda item: float(item["score"]), reverse=True)
    return results[:limit]


def _code_evidence(
    row: dict[str, Any],
    *,
    index_name: str,
    unit_type: str,
    score: float,
    source_type: str = "code_kb",
) -> dict[str, Any]:
    ref = str(row.get("record_id") or row.get("point_id") or row.get("quote_id") or row.get("usage_id") or row.get("configuration_model_id") or row.get("bitmask_id") or row.get("algorithm_id") or row.get("chain_id") or row.get("template_id") or "")
    title = str(row.get("title") or row.get("point_name") or row.get("referenced_display") or row.get("name") or row.get("tab_label") or row.get("chain_type") or row.get("file_name") or ref)
    risk_tags = list(row.get("risk_tags") or [])
    if unit_type == "configuration_model" and "device_count_topology" not in risk_tags:
        risk_tags.append("device_count_topology")
    return {
        "evidence_id": ref,
        "source_type": source_type,
        "source_path": str(row.get("source_path") or f"indexes/advisory/{index_name}_index.jsonl"),
        "ref": ref,
        "unit_type": unit_type,
        "title": title,
        "summary": _row_summary(row, unit_type),
        "score": round(score, 4),
        "risk_tags": risk_tags,
        "confidence": "medium",
        "template_id": row.get("template_id"),
        "project_type": row.get("project_type"),
        "function_type": row.get("function_type"),
    }


def _row_summary(row: dict[str, Any], unit_type: str) -> str:
    if unit_type == "template":
        return f"{row.get('file_name')}：{row.get('template_archetype')}，IO 绑定 {row.get('io_binding_level')}。"
    if unit_type == "tab":
        return f"{row.get('tab_label')} 页面角色为 {row.get('tab_role')}，节点数 {row.get('node_count')}。"
    if unit_type == "point":
        return f"{row.get('point_name')}，角色 {','.join(row.get('point_roles') or [])}，页面 {row.get('tab_label')}。"
    if unit_type == "quote_reference":
        return f"quote 引用 {row.get('referenced_display')}，原始节点 {row.get('referenced_node_id')}，页面 {row.get('referenced_tab_label')}。"
    if unit_type == "point_usage":
        return f"{row.get('point_name')} 定义 {row.get('definition_count')} 处，引用 {row.get('quote_count')} 处。"
    if unit_type == "subflow":
        return f"{row.get('record_type')} {row.get('name') or row.get('definition_name')}，实例数 {row.get('instance_count', '')}。"
    if unit_type == "configuration_model":
        return f"{row.get('tab_label')} 配置模型，影响 {','.join(row.get('affects') or [])}。"
    if unit_type == "bitmask_mapping":
        device_array = row.get("device_array") or {}
        return f"{row.get('node_type')} 设备位组合，类型 {device_array.get('equipment_type')}，最大数量 {device_array.get('max_count')}。"
    if unit_type == "algorithm_role":
        return f"{row.get('node_type')} 算法角色 {row.get('algorithm_role')}，页面 {row.get('tab_label')}。"
    if "chain" in unit_type:
        return str(row.get("summary") or "")
    return str(row.get("summary") or row.get("text_for_search") or "")[:180]


def _section_summary(card: DomainKnowledgeCard, section: str) -> str:
    content = re.sub(r"\s+", " ", card.sections.get(section, "")).strip()
    return content[:220]


def _infer_project_type(text: str, project_type: Any) -> str | None:
    if project_type in {"ahu", "plant_room"}:
        return str(project_type)
    if any(keyword in text for keyword in ["AHU", "ahu", "空调箱", "送风", "新风", "防冻", "过滤网"]):
        return "ahu"
    if any(keyword in text for keyword in ["机房", "群控", "水泵", "冷却塔", "旁通", "热泵", "主机"]):
        return "plant_room"
    return None


def _infer_question_type(text: str) -> str:
    if any(keyword in text for keyword in ["删", "删除", "取消", "旁路", "能不能删", "保护", "联锁"]):
        return "risk_review"
    if any(keyword in text for keyword in ["多少", "设定", "阈值", "PID", "参数"]):
        return "parameter_recommendation"
    if any(keyword in text for keyword in ["影响", "从哪里", "哪里用", "最终"]):
        return "impact_analysis"
    if any(keyword in text for keyword in ["调试", "验收", "检查"]):
        return "commissioning"
    if any(keyword in text for keyword in ["能不能自动改", "可不可以改", "能否修改"]):
        return "patchability"
    return "strategy_review"


def _infer_function_type(text: str, project_type: str | None) -> str:
    if any(keyword in text for keyword in ["数量", "台数", "2 台", "4 台", "几台"]) or ("台" in text and any(keyword in text for keyword in ["改", "增加", "减少"])):
        return "configuration_model"
    if "CO2" in text or "二氧化碳" in text:
        return "co2_control"
    if "送风" in text and "温度" in text:
        return "supply_air_temperature_control"
    if "防冻" in text:
        return "freeze_protection"
    if "过滤网" in text or "滤网" in text:
        return "filter_alarm"
    if "直膨" in text:
        return "dx_unit_control"
    if "旁通" in text:
        return "bypass_valve_control"
    if "压差" in text:
        return "differential_pressure_control"
    if "水泵" in text or "冷冻泵" in text or "冷却泵" in text:
        return "pump_control"
    if "冷却塔" in text:
        return "cooling_tower_control"
    if "热泵" in text or "主机" in text:
        return "chiller_sequence" if project_type == "plant_room" else "dx_unit_control"
    return "general"


def _infer_risk_tags(text: str, function_type: str) -> list[str]:
    tags: list[str] = []
    if function_type in {"freeze_protection"} or any(keyword in text for keyword in ["保护", "联锁", "故障", "防冻", "旁路", "删除", "删掉"]):
        tags.append("protection_logic")
    if any(keyword in text for keyword in ["点位", "硬接", "通道", "IO"]):
        tags.append("io_point_mapping")
    if any(keyword in text for keyword in ["Modbus", "BACnet", "通讯", "对象号", "地址"]):
        tags.append("communication_mapping")
    if any(keyword in text for keyword in ["数量", "台数", "2 台", "4 台", "位组合", "配置"]):
        tags.append("device_count_topology")
    if function_type in {"co2_control"}:
        tags.append("air_quality_logic")
    if function_type not in {"general"}:
        tags.append("actuator_logic")
    return _unique_terms(tags)


def _infer_equipment(text: str, function_type: str, project_type: str | None) -> list[str]:
    equipment: list[str] = []
    for label, keywords in [
        ("AHU", ["AHU", "ahu", "空调箱"]),
        ("送风机", ["送风机", "送风"]),
        ("新风阀", ["新风阀", "新风"]),
        ("过滤网", ["过滤网", "滤网"]),
        ("直膨机", ["直膨"]),
        ("主机", ["主机", "冷机"]),
        ("热泵", ["热泵"]),
        ("水泵", ["水泵", "冷冻泵", "冷却泵"]),
        ("冷却塔", ["冷却塔"]),
        ("旁通阀", ["旁通阀", "旁通"]),
    ]:
        if any(keyword in text for keyword in keywords):
            equipment.append(label)
    if function_type == "co2_control":
        equipment.extend(["AHU", "新风阀"])
    if function_type == "freeze_protection":
        equipment.extend(["AHU", "送风机", "新风阀"])
    if project_type == "plant_room" and function_type == "configuration_model":
        equipment.extend(["热泵", "水泵"])
    return _unique_terms(equipment)


def _infer_target_parameter(text: str) -> str | None:
    for keyword in ["CO2", "送风温度", "压差", "旁通", "频率", "阈值", "设定值"]:
        if keyword in text:
            return keyword
    return None


def _expected_tab_roles(function_type: str) -> set[str]:
    if function_type in {"co2_control", "supply_air_temperature_control", "freeze_protection", "dx_unit_control"}:
        return {"main_control", "io_communication", "dx_status_mapping", "fault_mapping"}
    if function_type in {"bypass_valve_control"}:
        return {"bypass_valve_control", "io_communication"}
    if function_type in {"pump_control", "chiller_sequence"}:
        return {"configuration", "equipment_standard_control", "system_sequence", "staging_rotation"}
    if function_type == "configuration_model":
        return {"configuration"}
    return set()


def _safe_load_index(name: str) -> list[dict[str, Any]]:
    try:
        return load_index(name)
    except AdvisoryKbLoadError:
        return []


def _json_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return str(value)


def _unique_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result
