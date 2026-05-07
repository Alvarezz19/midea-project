from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.services.knowledge import search_knowledge
from app.services.llm_gateway import LLMGatewayError, chat_json
from app.services.project_semantic_index import summarize_current_project_for_planner
from app.services.retrieval import RetrievalError, load_node_neighborhood
from app.services.semantic_locator import locate_semantic_targets, public_semantic_candidates


SOURCE_WEIGHTS = {
    "knowledge/AHU控制策略.md": 1.25,
    "knowledge/机房群控控制策略.md": 1.2,
    "knowledge/工程规范.md": 1.15,
    "knowledge/补丁配方.md": 0.9,
}


def answer_advisory_question(
    message: str,
    *,
    state: dict[str, Any],
    intent_result: dict[str, Any],
    provider: str | None = None,
) -> dict[str, Any]:
    """生成工程咨询建议。咨询结果只进入建议状态，不直接修改工程。"""

    context = build_advisory_context(message, state=state, intent_result=intent_result)
    baseline = _deterministic_advisory_result(message, context=context, intent_result=intent_result)
    if baseline.get("status") == "out_of_scope":
        baseline["context_used"] = _context_used_summary(context)
        baseline["created_at"] = _utc_now_iso()
        return baseline
    try:
        llm_answer = _answer_with_llm(message, context=context, provider=provider)
        result = _normalize_advisory_result(llm_answer, message=message, context=context, intent_result=intent_result)
    except LLMGatewayError:
        result = baseline
    result = _repair_advisory_result(result, baseline)
    result["answer"] = _ensure_no_modify_notice(result.get("answer"))
    result["context_used"] = _context_used_summary(context)
    result["created_at"] = _utc_now_iso()
    return result


def build_advisory_context(message: str, *, state: dict[str, Any], intent_result: dict[str, Any]) -> dict[str, Any]:
    queries = build_advisory_queries(message, intent_result=intent_result, project_type=state.get("project_type"))
    patch_support_queries = build_patch_support_queries(message, intent_result=intent_result)
    if intent_result.get("should_search_domain_knowledge", True):
        knowledge_items = _search_multi_query_knowledge(queries, include_recipes=False)
        patch_support_items = _search_multi_query_knowledge(patch_support_queries, limit=2, recipe_only=True)
    else:
        knowledge_items = []
        patch_support_items = []
    project_context: dict[str, Any] = {}
    project_path = state.get("current_project_path")
    if project_path and intent_result.get("should_load_project_context", True):
        project_context = _load_project_context(
            message,
            project_path=str(project_path),
            state=state,
            intent_result=intent_result,
        )
    return {
        "queries": queries,
        "patch_support_queries": patch_support_queries,
        "knowledge_context": knowledge_items,
        "patch_support_context": patch_support_items,
        "project_context": project_context,
        "conversation_context": {
            "requirement_slots": state.get("requirement_slots", {}),
            "design_brief": state.get("design_brief"),
            "recent_user_intents": state.get("recent_user_intents", [])[-5:],
            "pending_advice": state.get("pending_advice"),
            "advice_context_summary": state.get("advice_context_summary"),
            "last_patch_summary": state.get("last_patch_summary"),
        },
    }


def build_advisory_queries(message: str, *, intent_result: dict[str, Any], project_type: Any = None) -> list[str]:
    text = message.strip()
    topic = str(intent_result.get("topic") or text)
    queries = [text, topic]
    if project_type == "ahu" or any(keyword in text for keyword in ("AHU", "ahu", "送风", "CO2", "防冻", "过滤网", "新风")):
        _append_unique(queries, f"AHU {topic} 控制策略")
    if "送风" in text and "温度" in text:
        _append_unique(queries, "AHU 送风温度控制 设定值 PID 舒适性 节能 除湿")
    if "CO2" in text or "二氧化碳" in text:
        _append_unique(queries, "AHU CO2 浓度 新风阀 阈值 室内空气品质")
    if "过滤网" in text:
        _append_unique(queries, "AHU 过滤网报警 压差开关 压差传感器")
    if "防冻" in text:
        _append_unique(queries, "AHU 防冻保护 联锁 新风阀 热水阀 风机")
        _append_unique(queries, "工程规范 不可默认删除的保护逻辑")
    if any(keyword in text for keyword in ("保护", "联锁", "旁路", "删除", "删掉", "取消", "去掉")):
        _append_unique(queries, "工程规范 保护逻辑 联锁 禁止旁路 高风险")
    if any(keyword in text for keyword in ("机房", "水泵", "冷机", "冷却塔", "旁通")):
        _append_unique(queries, f"机房群控 {topic} 控制策略")
    _append_unique(queries, f"工程规范 {topic} 修改 风险")
    return [query for query in queries if query]


def build_patch_support_queries(message: str, *, intent_result: dict[str, Any]) -> list[str]:
    text = message.strip()
    topic = str(intent_result.get("topic") or text)
    queries: list[str] = []
    if "送风" in text and "温度" in text:
        _append_unique(queries, "补丁配方 AHU 送风温度 PID 动态设定")
    if "CO2" in text or "二氧化碳" in text:
        _append_unique(queries, "补丁配方 CO2 浓度设定 接入 新风阀")
    if "过滤网" in text:
        _append_unique(queries, "补丁配方 AHU 过滤网 报警 点位")
    if any(keyword in text for keyword in ("点位", "通道", "地址", "Modbus", "BACnet")):
        _append_unique(queries, "补丁配方 IO 通讯 点位 修改 风险")
    if any(keyword in text for keyword in ("接入", "接到", "新增", "修改计划", "采纳")):
        _append_unique(queries, f"补丁配方 {topic}")
    return queries


def update_advice_context_summary(state: dict[str, Any], advisory_result: dict[str, Any]) -> dict[str, Any]:
    previous = state.get("advice_context_summary") if isinstance(state.get("advice_context_summary"), dict) else {}
    topics = list(previous.get("topics") or [])
    topic = str(advisory_result.get("topic") or "").strip()
    if topic and topic not in topics:
        topics.append(topic)
    return {
        "topics": topics[-8:],
        "last_topic": topic or previous.get("last_topic"),
        "last_status": advisory_result.get("status"),
        "last_recommendation": advisory_result.get("recommendation"),
        "updated_at": _utc_now_iso(),
    }


def _answer_with_llm(message: str, *, context: dict[str, Any], provider: str | None) -> dict[str, Any]:
    system = (
        "你是楼宇自控工程顾问。只输出 JSON 对象。"
        "必须把“咨询是否可回答”和“补丁是否可执行”拆开判断："
        "status 只表示能否给工程设计建议；adoptable_patch_intent.executable 才表示能否转入补丁。"
        "找不到唯一节点、缺节点 ID、缺接线位置，只能让 adoptable_patch_intent.executable=false，不能因此把常见工程咨询降级为 needs_more_info。"
        "只有连行业建议、条件化建议或风险判断都无法给出时，status 才能是 needs_more_info。"
        "回答必须说明当前不会修改工程；必须包含依据、前提、风险和缺失信息。"
        "如果用户采纳，仍只能给 adoptable_patch_intent.message，后续必须走 planner、dry-run、风险确认和校验。"
        "不要输出完整工程 JSON，不要要求用户提供节点 ID。"
    )
    payload = {
        "user_message": message,
        "context": context,
        "output_rules": [
            "例：用户问“送风温度设定值多少比较好？”时，应 status=answered，给出条件化建议；若未定位唯一节点，则 adoptable_patch_intent.executable=false。",
            "例：用户问“防冻保护能不能删？”时，应 status=unsafe_request，说明不建议删除；adoptable_patch_intent.executable=false。",
            "例：用户问“Python 怎么写爬虫？”时，应 status=out_of_scope，且不生成候选需求。",
            "knowledge_context 是主咨询依据；patch_support_context 只用于判断是否能形成 adoptable_patch_intent，不要让补丁配方主导咨询答案。",
        ],
        "output_schema": {
            "status": "answered | needs_more_info | out_of_scope | unsafe_request",
            "answer": "string",
            "topic": "string",
            "recommendation": {"value": "number|string|null", "unit": "string|null", "range": "string|null", "confidence": "low|medium|high"},
            "basis": [{"source": "string", "summary": "string"}],
            "assumptions": ["string"],
            "risks": ["string"],
            "missing_info": ["string"],
            "candidate_requirements": [{"content": "string", "status": "candidate", "needs_confirmation": True}],
            "adoptable_patch_intent": {
                "executable": True,
                "message": "string",
                "reason": "string，必须只描述补丁可执行性，不要否定咨询回答本身",
            },
            "next_action": "review_advice",
        },
    }
    return chat_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        provider=provider,
        prompt_name="advisory_chat",
        temperature=0.2,
        max_tokens=1800,
    )


def _normalize_advisory_result(
    value: dict[str, Any],
    *,
    message: str,
    context: dict[str, Any],
    intent_result: dict[str, Any],
) -> dict[str, Any]:
    status = str(value.get("status") or "answered")
    if status not in {"answered", "needs_more_info", "out_of_scope", "unsafe_request"}:
        status = "answered"
    result = {
        "status": status,
        "answer": str(value.get("answer") or "").strip() or _fallback_answer_intro(message),
        "topic": str(value.get("topic") or intent_result.get("topic") or message).strip(),
        "recommendation": value.get("recommendation") if isinstance(value.get("recommendation"), dict) else {},
        "basis": _basis_list(value.get("basis")) or _basis_from_context(context),
        "assumptions": _string_list(value.get("assumptions")) or ["当前建议基于已提供的工程信息和本地知识库。"],
        "risks": _string_list(value.get("risks")) or ["采纳建议后仍需经过 dry-run、风险确认和工程校验。"],
        "missing_info": _string_list(value.get("missing_info")),
        "candidate_requirements": _candidate_requirements(value.get("candidate_requirements")),
        "adoptable_patch_intent": _adoptable_patch_intent(value.get("adoptable_patch_intent")),
        "next_action": str(value.get("next_action") or "review_advice"),
    }
    if isinstance(value.get("_llm_meta"), dict):
        result["llm_meta"] = value["_llm_meta"]
    return result


def _deterministic_advisory_result(message: str, *, context: dict[str, Any], intent_result: dict[str, Any]) -> dict[str, Any]:
    text = message.strip()
    topic = str(intent_result.get("topic") or text)
    answer = "这是设计建议问题，当前不会修改工程。"
    recommendation: dict[str, Any] = {"confidence": "medium"}
    assumptions = ["当前按常规楼宇自控工程场景分析。"]
    risks = ["采纳建议后仍必须进入结构化补丁、dry-run、风险确认和校验流程。"]
    missing_info = ["现场设计指标", "业主运行偏好", "设备和传感器实际点表"]
    adoptable = {"executable": False, "message": "", "reason": "当前建议没有形成可直接执行的明确修改值。"}
    candidates: list[dict[str, Any]] = []
    status = "answered"

    if "Python" in text or "爬虫" in text:
        return {
            "status": "out_of_scope",
            "answer": "这个问题不属于当前工程咨询范围，当前不会加载或修改工程。",
            "topic": topic,
            "recommendation": {},
            "basis": [],
            "assumptions": [],
            "risks": [],
            "missing_info": [],
            "candidate_requirements": [],
            "adoptable_patch_intent": adoptable,
            "next_action": "send_message",
        }

    if "送风" in text and "温度" in text:
        answer += " 如果是常规 AHU 舒适性控制，送风温度设定值可先按 24°C 考虑；节能优先可考虑 25-26°C；除湿或快速降温优先可考虑 22-23°C。"
        recommendation = {"value": 24, "unit": "°C", "range": "22-26°C", "confidence": "medium"}
        assumptions = ["常规 AHU 舒适性控制", "未提供特殊工艺温度要求", "当前工程存在送风温度或设定值相关控制对象时才可转为补丁计划。"]
        risks.extend(["设定过低会增加能耗。", "设定过高可能影响舒适性或除湿效果。"])
        missing_info = ["是否节能优先", "是否除湿优先", "是否有工艺温度要求"]
        candidates = [_candidate_requirement("送风温度设定值按 24°C 考虑")]
        adoptable = {"executable": True, "message": "把送风温度设定值改为 24°C", "reason": "建议值明确，可进入现有补丁规划链路。"}
    elif "CO2" in text or "二氧化碳" in text:
        answer += " 常规舒适性通风可先把 CO2 阈值放在 800-1000 ppm 区间；人员密度较高或空气品质优先时偏向 800 ppm，节能优先时可偏向 1000 ppm。"
        recommendation = {"value": 900, "unit": "ppm", "range": "800-1000 ppm", "confidence": "medium"}
        risks.extend(["阈值过低会增加新风负荷。", "阈值过高可能导致室内空气品质下降。"])
        missing_info = ["人员密度", "新风系统能力", "是否有空气品质硬性指标"]
        candidates = [_candidate_requirement("CO2 控制阈值按 900 ppm 作为初始建议")]
        adoptable = {"executable": True, "message": "把 CO2 设定值改为 900ppm", "reason": "建议阈值明确，可交给 planner 定位设定节点。"}
    elif "过滤网" in text:
        answer += " 医院或对空气品质敏感的 AHU 建议保留或增加过滤网报警，通常由压差开关或压差传感器触发，用于提示堵塞和维护。"
        recommendation = {"value": "保留或增加过滤网报警", "unit": "", "range": "", "confidence": "high"}
        assumptions = ["过滤网堵塞会影响风量和空气品质。"]
        risks.extend(["过滤网报警不能替代防冻、风机故障等安全保护。", "新增报警涉及点表时需要现场点位复核。"])
        missing_info = ["是否已有压差开关或压差传感器", "报警输出方式", "现场点表"]
        candidates = [_candidate_requirement("AHU 保留或增加过滤网报警")]
        adoptable = {"executable": False, "message": "增加过滤网报警", "reason": "需要先确认报警点位和接入页面，不能直接生成可执行补丁。"}
    elif "防冻" in text and any(keyword in text for keyword in ("删", "删除", "取消", "去掉", "能不能")):
        status = "unsafe_request"
        answer += " 不建议删除防冻保护。防冻属于 AHU 关键保护逻辑，触发时通常应停风机、关闭新风阀、打开热水阀或发出报警。"
        recommendation = {"value": "不删除防冻保护", "unit": "", "range": "", "confidence": "high"}
        assumptions = ["防冻保护用于避免低温冻裂和联锁失效。"]
        risks.extend(["删除或旁路防冻保护可能导致盘管冻裂。", "如确需调整，应先确认替代保护链路并走高风险确认。"])
        missing_info = ["现有防冻输入点", "触发后的联锁动作", "替代保护链路"]
        candidates = [_candidate_requirement("防冻保护应保留，不默认删除")]
        adoptable = {"executable": False, "message": "", "reason": "高风险保护删除不应由咨询建议直接转为补丁。"}
    elif any(keyword in text for keyword in ("联锁", "保护")) and any(keyword in text for keyword in ("删", "删除", "取消", "去掉", "旁路")):
        status = "unsafe_request"
        answer += " 不建议删除或旁路保护、联锁逻辑。此类逻辑通常承担设备安全、故障隔离或防误动作约束，应先确认替代保护链路。"
        recommendation = {"value": "保留保护联锁", "unit": "", "range": "", "confidence": "high"}
        assumptions = ["该问题涉及保护或联锁链路。"]
        risks.extend(["删除或旁路保护联锁可能导致设备损坏、误动作或安全约束失效。", "确需调整时必须按高风险补丁人工确认。"])
        missing_info = ["现有保护输入点", "联锁动作对象", "替代保护策略"]
        candidates = [_candidate_requirement("保护和联锁逻辑不默认删除或旁路")]
        adoptable = {"executable": False, "message": "", "reason": "保护联锁删除属于高风险，不应由咨询建议直接转为补丁。"}
    else:
        answer += " 这个问题与工程设计相关，但当前信息不足以给出唯一参数。建议先明确运行目标、现场点表和已有控制对象，再决定是否转入修改计划。"

    return {
        "status": status,
        "answer": answer,
        "topic": topic,
        "recommendation": recommendation,
        "basis": _basis_from_context(context),
        "assumptions": assumptions,
        "risks": risks,
        "missing_info": missing_info,
        "candidate_requirements": candidates,
        "adoptable_patch_intent": adoptable,
        "next_action": "review_advice",
    }


def _repair_advisory_result(result: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    baseline_patch = baseline.get("adoptable_patch_intent") if isinstance(baseline.get("adoptable_patch_intent"), dict) else {}
    result_patch = result.get("adoptable_patch_intent") if isinstance(result.get("adoptable_patch_intent"), dict) else {}
    if baseline.get("status") == "out_of_scope":
        return baseline
    if baseline.get("status") not in {"answered", "unsafe_request"}:
        return result

    repaired = dict(result)
    if result.get("status") == "needs_more_info" and _missing_info_is_only_execution_detail(result):
        repaired["status"] = baseline.get("status")
        repaired["answer"] = _combine_answer_with_baseline(result.get("answer"), baseline.get("answer"))
        repaired["next_action"] = "review_advice"
    if baseline.get("status") == "unsafe_request":
        repaired["status"] = "unsafe_request"
        repaired["adoptable_patch_intent"] = baseline_patch
    elif bool(baseline_patch.get("executable")) and not result_patch.get("executable"):
        repaired["adoptable_patch_intent"] = baseline_patch
    repaired["recommendation"] = _merge_recommendation(repaired.get("recommendation"), baseline.get("recommendation"))
    repaired["candidate_requirements"] = _merge_candidate_requirement_lists(
        repaired.get("candidate_requirements"),
        baseline.get("candidate_requirements"),
    )
    repaired["assumptions"] = _merge_string_lists(repaired.get("assumptions"), baseline.get("assumptions"), limit=6)
    repaired["risks"] = _merge_string_lists(repaired.get("risks"), baseline.get("risks"), limit=8)
    repaired["missing_info"] = _merge_string_lists(repaired.get("missing_info"), baseline.get("missing_info"), limit=8)
    return repaired


def _load_project_context(
    message: str,
    *,
    project_path: str,
    state: dict[str, Any],
    intent_result: dict[str, Any],
) -> dict[str, Any]:
    summary = summarize_current_project_for_planner(project_path, max_nodes=40, max_chars=9000)
    location = locate_semantic_targets(
        message,
        project_path=project_path,
        project_type=state.get("project_type"),
        template_id=state.get("selected_template_id"),
        conversation_context={
            "last_affected_node_ids": state.get("last_affected_node_ids", []),
            "last_touched_entities": state.get("last_touched_entities", []),
            "semantic_target_candidates": state.get("semantic_target_candidates", []),
        },
    )
    selected = location.get("selected") if isinstance(location.get("selected"), dict) else None
    anchor_id = None
    if selected and isinstance(selected.get("selector"), dict):
        anchor_id = selected["selector"].get("id")
    neighborhood: dict[str, Any] | None = None
    if isinstance(anchor_id, str) and anchor_id:
        try:
            neighborhood = load_node_neighborhood(project_path, [anchor_id], depth=1, max_nodes=24)
        except RetrievalError:
            neighborhood = None
    return {
        "project_type": state.get("project_type"),
        "project_id": state.get("current_project_id"),
        "version_id": state.get("current_project_version_id"),
        "summary": summary.get("summary"),
        "tabs": summary.get("tabs", [])[:12],
        "target_resolution": location,
        "semantic_candidates": public_semantic_candidates(location),
        "node_neighborhood": neighborhood,
        "topic": intent_result.get("topic"),
    }


def _search_multi_query_knowledge(
    queries: list[str],
    *,
    limit: int = 6,
    include_recipes: bool = True,
    recipe_only: bool = False,
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for query_index, query in enumerate(queries[:8]):
        for item in search_knowledge(query, limit=4, min_score=0.05):
            is_recipe = item.get("source_path") == "knowledge/补丁配方.md"
            if recipe_only and not is_recipe:
                continue
            if not include_recipes and is_recipe:
                continue
            weighted = _weighted_knowledge_item(item, query_index=query_index)
            key = str(item.get("chunk_id") or f"{item.get('source_path')}#{item.get('title')}")
            previous = by_key.get(key)
            if previous is None or float(weighted.get("weighted_score") or 0) > float(previous.get("weighted_score") or 0):
                by_key[key] = weighted
    return sorted(by_key.values(), key=lambda item: float(item.get("weighted_score") or item.get("score") or 0), reverse=True)[:limit]


def _weighted_knowledge_item(item: dict[str, Any], *, query_index: int) -> dict[str, Any]:
    source = str(item.get("source_path") or "")
    score = float(item.get("score") or 0)
    source_weight = SOURCE_WEIGHTS.get(source, 1.0)
    query_weight = max(0.72, 1.0 - query_index * 0.04)
    weighted = dict(item)
    weighted["source_weight"] = source_weight
    weighted["query_weight"] = round(query_weight, 3)
    weighted["weighted_score"] = round(score * source_weight * query_weight, 6)
    return weighted


def _context_used_summary(context: dict[str, Any]) -> dict[str, Any]:
    knowledge = context.get("knowledge_context") if isinstance(context.get("knowledge_context"), list) else []
    project_context = context.get("project_context") if isinstance(context.get("project_context"), dict) else {}
    sources = []
    for item in knowledge:
        if isinstance(item, dict):
            label = " / ".join(str(part) for part in (item.get("source_path"), item.get("title")) if part)
            if label and label not in sources:
                sources.append(label)
    return {
        "queries": context.get("queries", []),
        "patch_support_queries": context.get("patch_support_queries", []),
        "knowledge_sources": sources,
        "knowledge_item_count": len(knowledge),
        "top_weighted_score": max((float(item.get("weighted_score") or item.get("score") or 0) for item in knowledge if isinstance(item, dict)), default=0),
        "loaded_project_context": bool(project_context),
        "target_status": (project_context.get("target_resolution") or {}).get("status") if project_context else None,
    }


def _basis_from_context(context: dict[str, Any]) -> list[dict[str, str]]:
    basis: list[dict[str, str]] = []
    knowledge = context.get("knowledge_context") if isinstance(context.get("knowledge_context"), list) else []
    for item in knowledge[:3]:
        if not isinstance(item, dict):
            continue
        source = " / ".join(str(part) for part in (item.get("source_path"), item.get("title")) if part)
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if source:
            basis.append({"source": source, "summary": content[:120]})
    project_context = context.get("project_context") if isinstance(context.get("project_context"), dict) else {}
    if project_context:
        target = project_context.get("target_resolution") if isinstance(project_context.get("target_resolution"), dict) else {}
        basis.append(
            {
                "source": "current_project",
                "summary": f"当前工程版本已加载，语义定位状态为 {target.get('status') or 'unknown'}。",
            }
        )
    if not basis:
        basis.append({"source": "local_rules", "summary": "基于本地规则给出保守建议。"})
    return basis


def _basis_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if source or summary:
            result.append({"source": source, "summary": summary})
    return result


def _candidate_requirements(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        result.append(
            {
                "content": content,
                "status": str(item.get("status") or "candidate"),
                "needs_confirmation": bool(item.get("needs_confirmation", True)),
            }
        )
    return result


def _candidate_requirement(content: str) -> dict[str, Any]:
    return {"content": content, "status": "candidate", "needs_confirmation": True}


def _adoptable_patch_intent(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"executable": False, "message": "", "reason": "未生成可执行修改意图。"}
    return {
        "executable": bool(value.get("executable")),
        "message": str(value.get("message") or "").strip(),
        "reason": str(value.get("reason") or "").strip(),
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _missing_info_is_only_execution_detail(result: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(result.get("answer") or ""),
            " ".join(_string_list(result.get("missing_info"))),
            str((result.get("adoptable_patch_intent") or {}).get("reason") if isinstance(result.get("adoptable_patch_intent"), dict) else ""),
        ]
    )
    execution_tokens = ("节点", "节点ID", "节点 ID", "位置", "接线", "点位", "通道", "目标", "planner", "补丁", "dry-run")
    domain_answer_tokens = ("典型", "范围", "建议", "可考虑", "通常", "不建议", "风险", "阈值", "设定")
    return any(token in text for token in execution_tokens) and any(token in text for token in domain_answer_tokens)


def _combine_answer_with_baseline(answer: Any, baseline_answer: Any) -> str:
    primary = str(answer or "").strip()
    baseline = str(baseline_answer or "").strip()
    if not primary:
        return baseline
    if "当前不会修改工程" not in primary and "当前不会修改工程" in baseline:
        primary = "这是设计建议问题，当前不会修改工程。" + primary
    if _contains_recommendation(primary):
        return primary
    return f"{primary}\n\n参考基线：{baseline}" if baseline else primary


def _ensure_no_modify_notice(answer: Any) -> str:
    text = str(answer or "").strip()
    notice = "这是设计建议问题，当前不会修改工程。"
    if not text:
        return notice
    if "当前不会修改工程" in text or "不会直接修改工程" in text:
        return text
    return notice + text


def _contains_recommendation(value: str) -> bool:
    return any(token in value for token in ("建议", "可考虑", "范围", "通常", "不建议", "优先"))


def _merge_recommendation(left: Any, right: Any) -> dict[str, Any]:
    if not isinstance(left, dict) or not left:
        return right if isinstance(right, dict) else {}
    if not isinstance(right, dict):
        return left
    merged = dict(left)
    for key, value in right.items():
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value
    return merged


def _merge_candidate_requirement_lists(left: Any, right: Any) -> list[dict[str, Any]]:
    result = _candidate_requirements(left)
    seen = {str(item.get("content")) for item in result if item.get("content")}
    for item in _candidate_requirements(right):
        content = str(item.get("content") or "")
        if content and content not in seen:
            seen.add(content)
            result.append(item)
    return result


def _merge_string_lists(left: Any, right: Any, *, limit: int) -> list[str]:
    result: list[str] = []
    for item in _string_list(left) + _string_list(right):
        if item not in result:
            result.append(item)
    return result[:limit]


def _fallback_answer_intro(message: str) -> str:
    return f"这是设计建议问题，当前不会修改工程。关于“{message}”，建议先结合当前工程对象、现场点表和运行目标复核。"


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
