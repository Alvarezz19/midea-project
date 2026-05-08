from __future__ import annotations

from typing import Any

from app.services.advisory_kb.fusion import fuse_evidence, fusion_summary
from app.services.advisory_kb.retrievers import (
    build_query_plan,
    current_project_refs_from_project_context,
    retrieve_code_units,
    retrieve_control_chains,
    retrieve_domain_cards,
    retrieve_parameter_stats,
    retrieve_protection_chains,
    retrieve_risk_rules,
)


def build_advisory_kb_context(
    message: str,
    *,
    state: dict[str, Any],
    intent_result: dict[str, Any],
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = build_query_plan(message, project_type=state.get("project_type"), intent_result=intent_result)
    domain_units = retrieve_domain_cards(plan)
    code_units = retrieve_code_units(plan)
    control_chains = retrieve_control_chains(plan)
    protection_chains = retrieve_protection_chains(plan)
    parameter_stats = retrieve_parameter_stats(plan)
    risk_rules = retrieve_risk_rules(plan)
    current_refs = current_project_refs_from_project_context(project_context)
    current_units = [
        {
            "evidence_id": f"current_project:{item.get('kind')}:{item.get('id') or item.get('display_name')}",
            "source_type": "current_project",
            "source_path": "current_project",
            "ref": str(item.get("id") or item.get("display_name") or ""),
            "title": str(item.get("display_name") or item.get("id") or ""),
            "summary": f"当前工程引用：{item.get('kind')} {item.get('display_name') or item.get('id')}",
            "score": 12.0,
            "risk_tags": [],
            "confidence": "high",
        }
        for item in current_refs[:5]
    ]
    retrieved_units = fuse_evidence(
        current_units + domain_units + risk_rules + code_units + parameter_stats + control_chains + protection_chains,
        limit=14,
    )
    return {
        "query_plan": plan.to_dict(),
        "retrieved_units": retrieved_units,
        "current_project_refs": current_refs,
        "control_chains": control_chains,
        "protection_chains": protection_chains,
        "parameter_stats": parameter_stats,
        "risk_rules": risk_rules,
        "fusion_summary": fusion_summary(retrieved_units),
    }
