from __future__ import annotations

from typing import Any


SOURCE_PRIORITY = {
    "current_project": 5.0,
    "domain_kb": 4.0,
    "rule": 4.0,
    "code_kb": 3.0,
    "parameter_stat": 2.6,
    "generated_summary": 1.5,
}

RISK_BOOST = {
    "protection_logic": 2.0,
    "io_point_mapping": 1.6,
    "communication_mapping": 1.6,
    "device_count_topology": 1.5,
    "actuator_logic": 1.1,
}


def fuse_evidence(units: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    best_by_key: dict[str, dict[str, Any]] = {}
    for rank, unit in enumerate(units):
        key = str(unit.get("evidence_id") or unit.get("ref") or f"unit-{rank}")
        scored = dict(unit)
        base_score = float(scored.get("score") or 0)
        source_type = str(scored.get("source_type") or "")
        priority = SOURCE_PRIORITY.get(source_type, 1.0)
        risk_boost = max((RISK_BOOST.get(str(tag), 0.0) for tag in scored.get("risk_tags") or []), default=0.0)
        scored["fusion_score"] = round(base_score + priority + risk_boost + (1.0 / (rank + 1)), 6)
        previous = best_by_key.get(key)
        if previous is None or float(scored["fusion_score"]) > float(previous.get("fusion_score") or 0):
            best_by_key[key] = scored
    fused = sorted(best_by_key.values(), key=lambda item: float(item.get("fusion_score") or 0), reverse=True)
    return fused[:limit]


def fusion_summary(units: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    risk_tags: list[str] = []
    for unit in units:
        source_type = str(unit.get("source_type") or "unknown")
        source_counts[source_type] = source_counts.get(source_type, 0) + 1
        for tag in unit.get("risk_tags") or []:
            tag_text = str(tag)
            if tag_text and tag_text not in risk_tags:
                risk_tags.append(tag_text)
    return {
        "unit_count": len(units),
        "source_counts": source_counts,
        "risk_tags": risk_tags[:12],
        "top_sources": [str(unit.get("source_type") or "") for unit in units[:5]],
    }
