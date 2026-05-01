from __future__ import annotations

import json
from typing import Any, Literal

from app.services.requirement_model import requirement_summary_from_slots


ConformanceStatus = Literal["covered", "partial", "missing"]


def build_requirement_conformance_report(
    *,
    nodes: list[dict[str, Any]],
    requirement_slots: dict[str, Any] | None = None,
    design_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """基于需求上下文和工程 JSON 生成需求覆盖报告。"""

    if not requirement_slots and not design_brief:
        return {
            "context_available": False,
            "status": "not_reviewed",
            "valid_for_requirement": False,
            "covered": [],
            "partial": [],
            "missing": [],
            "blocked": [],
            "evidence": [],
            "warnings": ["需求覆盖未复核：未找到会话、项目或版本的需求上下文。"],
            "summary": {"covered_count": 0, "partial_count": 0, "missing_count": 0, "blocked_count": 0},
        }

    requirements = _requirements_from_context(requirement_slots, design_brief)
    corpus = _ProjectCorpus(nodes)
    covered: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    blocked: list[str] = []

    for requirement in requirements:
        result = _check_requirement(requirement, corpus)
        entry = {
            "requirement": requirement["label"],
            "category": requirement["category"],
            "status": result["status"],
            "evidence": result["evidence"],
            "reason": result["reason"],
        }
        if result["status"] == "covered":
            covered.append(entry)
        elif result["status"] == "missing":
            missing.append(entry)
            if result["blocking"]:
                blocked.append(result["reason"])
        else:
            partial.append(entry)
        evidence.extend(_evidence_rows(requirement, result))

    blocked = _unique_strings(blocked)
    status = "blocked" if blocked else "passed"
    return {
        "context_available": True,
        "status": status,
        "valid_for_requirement": not blocked,
        "covered": covered,
        "partial": partial,
        "missing": missing,
        "blocked": blocked,
        "evidence": evidence,
        "warnings": [],
        "summary": {
            "covered_count": len(covered),
            "partial_count": len(partial),
            "missing_count": len(missing),
            "blocked_count": len(blocked),
        },
    }


def merge_conformance_into_validation(report: dict[str, Any], conformance_report: dict[str, Any]) -> dict[str, Any]:
    """把需求覆盖门禁结果合并到现有工程校验报告。"""

    merged = dict(report)
    merged["conformance_report"] = conformance_report
    summary = dict(merged.get("summary") or {})
    conformance_summary = conformance_report.get("summary") if isinstance(conformance_report.get("summary"), dict) else {}
    summary["conformance_missing_count"] = conformance_summary.get("missing_count", 0)
    summary["conformance_blocked_count"] = conformance_summary.get("blocked_count", 0)
    merged["summary"] = summary

    blocked_reasons = list(merged.get("blocked_export_reasons") or [])
    if conformance_report.get("context_available"):
        for reason in conformance_report.get("blocked") or []:
            blocked_reasons.append(f"需求覆盖未通过：{reason}")
        if conformance_report.get("blocked"):
            merged["exportable"] = False
    else:
        merged.setdefault("warnings", [])
        if isinstance(merged["warnings"], list):
            merged["warnings"].extend(conformance_report.get("warnings") or [])
    merged["blocked_export_reasons"] = _unique_strings(blocked_reasons)
    return merged


def _requirements_from_context(
    requirement_slots: dict[str, Any] | None,
    design_brief: dict[str, Any] | None,
) -> list[dict[str, str]]:
    requirements: list[dict[str, str]] = []
    if requirement_slots:
        summary = requirement_summary_from_slots(requirement_slots)
        _extend_requirements(requirements, "equipment", summary.get("equipment"))
        _extend_requirements(requirements, "control", summary.get("control_features"))
        _extend_requirements(requirements, "point", [*summary.get("communication", []), *summary.get("io_points", [])])
        _extend_requirements(requirements, "protection", summary.get("protection_logic"))
    if design_brief:
        _extend_requirements(requirements, "equipment", design_brief.get("equipment_plan"))
        _extend_requirements(requirements, "control", design_brief.get("control_plan"))
        _extend_requirements(requirements, "point", design_brief.get("point_plan"))
        _extend_requirements(requirements, "protection", design_brief.get("protection_plan"))
    return _dedupe_requirements(requirements)


def _extend_requirements(target: list[dict[str, str]], category: str, values: Any) -> None:
    for value in _string_list(values):
        target.append({"category": category, "label": value})


def _dedupe_requirements(requirements: list[dict[str, str]]) -> list[dict[str, str]]:
    selected: dict[tuple[str, str], dict[str, str]] = {}
    for requirement in requirements:
        key = _requirement_identity(requirement)
        existing = selected.get(key)
        if existing is None or _requirement_priority(requirement) > _requirement_priority(existing):
            selected[key] = requirement
    return list(selected.values())


def _requirement_identity(requirement: dict[str, str]) -> tuple[str, str]:
    explicit_rule = _explicit_blocking_rule(requirement["label"])
    if explicit_rule:
        return ("blocking", str(explicit_rule["id"]))
    return (requirement["category"], requirement["label"])


def _requirement_priority(requirement: dict[str, str]) -> int:
    category_order = {"equipment": 1, "point": 2, "control": 3, "protection": 4}
    priority = category_order.get(requirement["category"], 0)
    if any(token in requirement["label"] for token in ("保护", "报警", "故障", "反馈")):
        priority += 2
    return priority


class _ProjectCorpus:
    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.rows = [_node_text(node) for node in nodes]
        self.full_text = "\n".join(row["text"] for row in self.rows)

    def has_any(self, terms: tuple[str, ...]) -> bool:
        return any(term in self.full_text for term in terms)

    def has_all_groups(self, groups: tuple[tuple[str, ...], ...]) -> bool:
        return all(self.has_any(group) for group in groups)

    def evidence_for(self, terms: tuple[str, ...], *, limit: int = 3) -> list[dict[str, str]]:
        evidence: list[dict[str, str]] = []
        for row in self.rows:
            if any(term in row["text"] for term in terms):
                evidence.append({key: value for key, value in row.items() if key != "text"})
            if len(evidence) >= limit:
                break
        return evidence


def _check_requirement(requirement: dict[str, str], corpus: _ProjectCorpus) -> dict[str, Any]:
    label = requirement["label"]
    category = requirement["category"]
    explicit_rule = _explicit_blocking_rule(label)
    if explicit_rule:
        covered = corpus.has_all_groups(explicit_rule["groups"])
        evidence_terms = tuple(term for group in explicit_rule["groups"] for term in group)
        evidence = corpus.evidence_for(evidence_terms)
        if covered:
            return _result("covered", evidence, f"{label} 已找到工程线索。", blocking=False)
        return _result("missing", [], explicit_rule["message"], blocking=True)

    terms = _terms_for_requirement(label)
    if terms and corpus.has_any(terms):
        return _result("covered", corpus.evidence_for(terms), f"{label} 已找到工程线索。", blocking=False)
    if category in {"equipment", "point"}:
        return _result("partial", [], f"{label} 未自动定位到明确证据，需人工复核。", blocking=False)
    return _result("partial", [], f"{label} 无法自动证明，需人工复核。", blocking=False)


def _explicit_blocking_rule(label: str) -> dict[str, Any] | None:
    rules = [
        {
            "id": "ahu_freeze_protection",
            "tokens": ("防冻",),
            "groups": (("防冻", "低温", "防霜"), ("保护", "报警", "故障", "联锁")),
            "message": "用户要求 AHU 防冻保护，但工程中无防冻保护线索。",
        },
        {
            "id": "ahu_filter_alarm",
            "tokens": ("过滤网", "滤网"),
            "groups": (("过滤", "滤网"), ("报警", "故障", "压差")),
            "message": "用户要求过滤网报警，但工程中无过滤网报警线索。",
        },
        {
            "id": "plant_pump_fault_feedback",
            "tokens": ("水泵故障", "水泵运行反馈", "泵故障", "泵运行反馈"),
            "groups": (("水泵", "泵"), ("故障", "报警"), ("运行", "反馈")),
            "message": "用户要求机房水泵故障/运行反馈，但水泵相关逻辑无故障或运行反馈线索。",
        },
        {
            "id": "plant_bypass_pressure",
            "tokens": ("旁通阀压差", "旁通压差"),
            "groups": (("旁通",), ("压差", "差压"), ("PID", "限幅", "上限", "下限", "控制")),
            "message": "用户要求旁通阀压差控制，但工程中缺少旁通、压差或控制线索。",
        },
    ]
    for rule in rules:
        if any(token in label for token in rule["tokens"]):
            return rule
    if "水泵" in label and ("故障" in label or "反馈" in label):
        return rules[2]
    if "旁通" in label and ("压差" in label or "差压" in label):
        return rules[3]
    return None


def _terms_for_requirement(label: str) -> tuple[str, ...]:
    terms = [label]
    synonyms = {
        "Modbus": ("Modbus", "modbus"),
        "BACnet": ("BACnet", "BACIP", "bacnet"),
        "CO2": ("CO2", "二氧化碳"),
        "直膨机": ("直膨", "DX"),
        "排风机": ("排风",),
        "新风阀": ("新风",),
        "回风阀": ("回风",),
        "水阀": ("水阀", "冷水阀", "热水阀"),
        "电加热": ("电加热",),
        "水泵": ("水泵", "泵"),
        "旁通阀": ("旁通",),
        "冷却塔": ("冷却塔",),
        "主机": ("主机", "冷机", "冷水机组"),
        "运行反馈": ("运行", "反馈"),
        "故障报警": ("故障", "报警"),
        "硬接 IO": ("hwInput", "hwOutput", "硬接", "IO"),
    }
    for key, values in synonyms.items():
        if key in label:
            terms.extend(values)
    normalized = label.replace(" ", "")
    if normalized != label:
        terms.append(normalized)
    return tuple(_unique_strings(terms))


def _result(status: ConformanceStatus, evidence: list[dict[str, str]], reason: str, *, blocking: bool) -> dict[str, Any]:
    return {"status": status, "evidence": evidence, "reason": reason, "blocking": blocking}


def _evidence_rows(requirement: dict[str, str], result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in result.get("evidence") or []:
        rows.append(
            {
                "requirement": requirement["label"],
                "category": requirement["category"],
                "status": result["status"],
                "node": item,
            }
        )
    return rows


def _node_text(node: dict[str, Any]) -> dict[str, str]:
    fields = {
        "node_id": str(node.get("id") or ""),
        "type": str(node.get("type") or ""),
        "label": str(node.get("label") or node.get("name") or ""),
        "tab_id": str(node.get("z") or ""),
    }
    text_parts = [fields["node_id"], fields["type"], fields["label"]]
    for key, value in node.items():
        if key in {"id", "type", "label", "name", "x", "y", "wires"}:
            continue
        if isinstance(value, (str, int, float, bool)):
            text_parts.append(str(value))
        elif isinstance(value, dict):
            text_parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
    fields["text"] = " ".join(text_parts)
    return fields


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _unique_strings(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
