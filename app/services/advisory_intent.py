from __future__ import annotations

import json
import re
from typing import Any

from app.services.llm_gateway import LLMGatewayError, chat_json


ADVISORY_INTENTS = {"advisory_intent", "domain_question", "out_of_scope"}

DOMAIN_KEYWORDS = (
    "AHU",
    "ahu",
    "空调箱",
    "新风",
    "风阀",
    "回风",
    "送风",
    "排风",
    "CO2",
    "二氧化碳",
    "防冻",
    "过滤网",
    "压差",
    "PID",
    "pid",
    "阈值",
    "设定值",
    "点位",
    "Modbus",
    "BACnet",
    "机房",
    "群控",
    "水泵",
    "冷机",
    "冷却塔",
    "旁通阀",
    "联锁",
    "保护",
)

INSPECTION_MARKERS = (
    "有没有",
    "是否",
    "是不是",
    "在哪",
    "哪里",
    "接到哪里",
    "接入了吗",
    "接到了吗",
    "连接了吗",
    "实例化",
    "子流程定义",
    "局部节点",
    "说明证据",
    "证据",
    "链路",
    "当前工程",
)

ADVISORY_MARKERS = (
    "比较好",
    "合适",
    "一般",
    "建议",
    "要不要",
    "需不需要",
    "是否需要",
    "能不能",
    "可不可以",
    "该不该",
    "多少",
    "怎么设计",
    "如何设计",
    "优先",
)

PATCH_MARKERS = (
    "改为",
    "改成",
    "调整为",
    "设为",
    "设置为",
    "替换为",
    "改名",
    "重命名",
    "命名为",
    "新增",
    "添加",
    "接入",
    "接到",
    "断开",
)

OUT_OF_SCOPE_KEYWORDS = ("Python", "python", "爬虫", "股票", "旅游", "天气", "电影", "游戏", "论文润色", "做饭")

ACCEPTANCE_PATTERNS = (
    r"采纳",
    r"按你说的.*改",
    r"就按.*改",
    r"照.*改",
    r"生成修改计划",
    r"按这个.*做",
    r"采用这个",
)

REJECTION_PATTERNS = (
    r"先不改",
    r"暂不",
    r"不采用",
    r"不采纳",
    r"不用改",
    r"不要改",
    r"取消建议",
)

EXPLICIT_PATCH_PATTERNS = (
    r"^(?:请|帮我|麻烦)?\s*(?:把|将).*(?:改为|改成|调整为|设为|设置为|替换为|接入|接到|连接|断开)",
    r"^(?:请|帮我|麻烦)?\s*(?:新增|添加|复制|断开)",
    r".*(?:改为|改成|调整为|设为|设置为|替换为)\s*[^吗？?]+$",
)

INSPECTION_PATTERNS = (
    r"(?:有没有|是否|是不是|是否真正|是不是真正).*(?:接入|接到|连接|实例化)",
    r"(?:接入|接到|连接).*(?:了吗|了没|没有|哪里|哪|是否|是不是)",
    r"(?:在哪|哪里).*(?:页面|节点|链路|输出|输入|点位)",
    r"(?:请)?说明.*(?:证据|链路|依据)",
    r"(?:只保留|还是).*(?:子流程定义|局部节点)",
)


def classify_advisory_intent(
    message: str,
    *,
    pending_advice: dict[str, Any] | None = None,
    project_path: str | None = None,
    project_type: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """识别咨询、采纳、拒绝和明确修改意图。"""

    rule_result = classify_advisory_intent_by_rules(
        message,
        pending_advice=pending_advice,
        project_path=project_path,
        project_type=project_type,
    )
    if rule_result["confidence"] >= 0.8 or rule_result["intent"] in {"advice_acceptance", "advice_override", "advice_rejection"}:
        return rule_result
    try:
        llm_result = _classify_with_llm(
            message,
            pending_advice=pending_advice,
            project_path=project_path,
            project_type=project_type,
            provider=provider,
        )
    except LLMGatewayError:
        return rule_result
    return _merge_rule_and_llm(rule_result, llm_result)


def classify_advisory_intent_by_rules(
    message: str,
    *,
    pending_advice: dict[str, Any] | None = None,
    project_path: str | None = None,
    project_type: str | None = None,
) -> dict[str, Any]:
    text = message.strip()
    has_pending = isinstance(pending_advice, dict) and bool(pending_advice)
    domain_related = _contains_any(text, DOMAIN_KEYWORDS)
    advisory_like = _contains_any(text, ADVISORY_MARKERS) or text.endswith(("?", "？"))
    patch_like = _contains_any(text, PATCH_MARKERS)
    inspection_like = _is_inspection_question(text, domain_related=domain_related)
    explicit_patch = _is_explicit_patch_command(text)
    out_of_scope = _contains_any(text, OUT_OF_SCOPE_KEYWORDS) and not domain_related

    if has_pending and _matches_any(text, REJECTION_PATTERNS):
        status = "candidate" if "记" in text else "rejected"
        return _result(
            "advice_rejection",
            relevance="current_project",
            confidence=0.95,
            topic=_pending_topic(pending_advice),
            reason="用户明确表示暂不采纳上一轮建议。",
            should_collect_candidate_requirement=status == "candidate",
            extra={"pending_advice_status": status},
        )

    override_message = _advice_override_message(text, pending_advice)
    if has_pending and override_message:
        return _result(
            "advice_override",
            relevance="current_project",
            confidence=0.9,
            topic=_pending_topic(pending_advice),
            reason="用户在采纳建议时给出了覆盖值。",
            extra={"override_message": override_message},
        )

    if has_pending and _matches_any(text, ACCEPTANCE_PATTERNS):
        return _result(
            "advice_acceptance",
            relevance="current_project",
            confidence=0.94,
            topic=_pending_topic(pending_advice),
            reason="用户明确采纳上一轮咨询建议。",
        )

    if out_of_scope:
        return _result(
            "out_of_scope",
            relevance="none",
            confidence=0.92,
            topic=_short_topic(text),
            reason="问题不属于 HVAC、群控或当前工程设计范围。",
            should_load_project_context=False,
            should_search_domain_knowledge=False,
            should_collect_candidate_requirement=False,
        )

    if inspection_like and not explicit_patch:
        return _result(
            "advisory_intent" if project_path else "domain_question",
            relevance="current_project" if project_path else "domain",
            confidence=0.9,
            topic=_short_topic(text),
            reason="用户在询问当前工程结构、链路或证据，属于工程审查咨询，不是落盘修改。",
            related_entities=_matched_keywords(text, DOMAIN_KEYWORDS),
            extra={"inspection_query": True},
        )

    if inspection_like and explicit_patch:
        return _result(
            "uncertain",
            relevance="current_project" if project_path else "domain",
            confidence=0.62,
            topic=_short_topic(text),
            reason="用户输入同时包含工程审查语气和明确修改动作，需要 LLM 进一步判定。",
            related_entities=_matched_keywords(text, DOMAIN_KEYWORDS),
            should_collect_candidate_requirement=False,
            extra={"inspection_query": True, "patch_marker_conflict": True},
        )

    if domain_related and advisory_like:
        return _result(
            "advisory_intent" if project_path else "domain_question",
            relevance="current_project" if project_path else "domain",
            confidence=0.88,
            topic=_short_topic(text),
            reason="问题涉及工程领域判断或设计建议，并非明确落盘修改。",
            related_entities=_matched_keywords(text, DOMAIN_KEYWORDS),
        )

    if domain_related and not patch_like:
        return _result(
            "domain_question",
            relevance="current_project" if project_path else "domain",
            confidence=0.72,
            topic=_short_topic(text),
            reason="问题与工程领域相关，但没有明确修改动作。",
            related_entities=_matched_keywords(text, DOMAIN_KEYWORDS),
        )

    if patch_like:
        return _result(
            "patch_intent",
            relevance="current_project" if project_path else "domain",
            confidence=0.86,
            topic=_short_topic(text),
            reason="用户输入包含明确修改动作。",
            should_collect_candidate_requirement=False,
        )

    return _result(
        "uncertain",
        relevance="current_project" if project_path or project_type else "unknown",
        confidence=0.48,
        topic=_short_topic(text),
        reason="规则无法稳定判断是否为工程咨询或修改请求。",
    )


def _classify_with_llm(
    message: str,
    *,
    pending_advice: dict[str, Any] | None,
    project_path: str | None,
    project_type: str | None,
    provider: str | None,
) -> dict[str, Any]:
    system = (
        "你是楼宇自控工程智能体的意图路由器。只输出 JSON 对象。"
        "intent 只能是 patch_intent、advisory_intent、advice_acceptance、advice_override、"
        "advice_rejection、domain_question、out_of_scope、uncertain。"
        "咨询建议不得路由成补丁；用户明确采纳上一轮建议才是 advice_acceptance。"
        "区分工程审查疑问和修改命令：'有没有接到/是否接入/接到哪里/是否实例化/请说明证据' 是 advisory_intent；"
        "'新增/把X接到Y/断开/改为' 这类命令式表达才是 patch_intent。"
    )
    payload = {
        "user_message": message,
        "has_current_project": bool(project_path),
        "project_type": project_type,
        "pending_advice": _compact_pending_advice(pending_advice),
        "output_schema": {
            "intent": "string",
            "relevance": "current_project | domain | none | unknown",
            "confidence": 0.0,
            "topic": "string",
            "reason": "string",
            "related_entities": ["string"],
            "should_load_project_context": True,
            "should_search_domain_knowledge": True,
            "should_collect_candidate_requirement": True,
            "override_message": "string | null",
        },
    }
    result = chat_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        provider=provider,
        prompt_name="advisory_intent",
        temperature=0,
        max_tokens=900,
    )
    return result


def _merge_rule_and_llm(rule_result: dict[str, Any], llm_result: dict[str, Any]) -> dict[str, Any]:
    intent = str(llm_result.get("intent") or rule_result["intent"])
    if intent not in {
        "patch_intent",
        "advisory_intent",
        "advice_acceptance",
        "advice_override",
        "advice_rejection",
        "domain_question",
        "out_of_scope",
        "uncertain",
    }:
        intent = rule_result["intent"]
    if rule_result.get("inspection_query") and intent == "patch_intent" and not rule_result.get("patch_marker_conflict"):
        intent = "advisory_intent" if rule_result.get("relevance") == "current_project" else "domain_question"
    confidence = _float_between(llm_result.get("confidence"), default=rule_result["confidence"])
    merged = {
        **rule_result,
        "intent": intent,
        "relevance": str(llm_result.get("relevance") or rule_result.get("relevance") or "unknown"),
        "confidence": confidence,
        "topic": str(llm_result.get("topic") or rule_result.get("topic") or ""),
        "reason": str(llm_result.get("reason") or rule_result.get("reason") or ""),
        "related_entities": _string_list(llm_result.get("related_entities")) or rule_result.get("related_entities", []),
        "should_load_project_context": bool(llm_result.get("should_load_project_context", rule_result.get("should_load_project_context"))),
        "should_search_domain_knowledge": bool(llm_result.get("should_search_domain_knowledge", rule_result.get("should_search_domain_knowledge"))),
        "should_collect_candidate_requirement": bool(
            llm_result.get("should_collect_candidate_requirement", rule_result.get("should_collect_candidate_requirement"))
        ),
    }
    if isinstance(llm_result.get("override_message"), str) and llm_result["override_message"].strip():
        merged["override_message"] = llm_result["override_message"].strip()
    if isinstance(llm_result.get("_llm_meta"), dict):
        merged["llm_meta"] = llm_result["_llm_meta"]
    return merged


def _result(
    intent: str,
    *,
    relevance: str,
    confidence: float,
    topic: str,
    reason: str,
    related_entities: list[str] | None = None,
    should_load_project_context: bool = True,
    should_search_domain_knowledge: bool = True,
    should_collect_candidate_requirement: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "relevance": relevance,
        "confidence": confidence,
        "topic": topic,
        "reason": reason,
        "related_entities": related_entities or [],
        "should_load_project_context": should_load_project_context,
        "should_search_domain_knowledge": should_search_domain_knowledge,
        "should_collect_candidate_requirement": should_collect_candidate_requirement,
        **(extra or {}),
    }


def _advice_override_message(message: str, pending_advice: dict[str, Any] | None) -> str | None:
    if not isinstance(pending_advice, dict):
        return None
    match = re.search(r"(?:改成|改为|设为|设置为|按)\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>°?C|℃|ppm|Pa|%|吧)?", message)
    if not match:
        return None
    topic = _pending_topic(pending_advice)
    value = match.group("value")
    unit = match.group("unit") or ""
    if "CO2" in topic or "二氧化碳" in topic:
        return f"把 CO2 设定值改为 {value}{unit or 'ppm'}"
    if "温度" in topic or "送风" in topic:
        return f"把送风温度设定值改为 {value}{unit or '°C'}"
    return f"{topic} 改为 {value}{unit}"


def _pending_topic(pending_advice: dict[str, Any] | None) -> str:
    if not isinstance(pending_advice, dict):
        return ""
    topic = pending_advice.get("topic") or pending_advice.get("content") or ""
    return str(topic)


def _compact_pending_advice(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "topic": value.get("topic"),
        "status": value.get("status"),
        "adoptable_patch_intent": value.get("adoptable_patch_intent"),
        "recommendation": value.get("recommendation"),
    }


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _is_inspection_question(text: str, *, domain_related: bool) -> bool:
    if not domain_related:
        return False
    if _contains_any(text, INSPECTION_MARKERS):
        return True
    if _matches_any(text, INSPECTION_PATTERNS):
        return True
    return text.endswith(("?", "？")) and any(token in text for token in ("接入", "接到", "连接", "实例化", "证据", "链路"))


def _is_explicit_patch_command(text: str) -> bool:
    if _matches_any(text, EXPLICIT_PATCH_PATTERNS):
        return True
    return any(text.startswith(prefix) for prefix in ("新增", "添加", "复制", "断开", "把", "将"))


def _matched_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for keyword in keywords:
        if keyword in text and keyword not in result:
            result.append(keyword)
    return result[:8]


def _short_topic(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" ?？。")
    return cleaned[:40]


def _float_between(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
