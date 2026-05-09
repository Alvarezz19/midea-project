from __future__ import annotations

import json
from pathlib import Path

from app.services.advisory_kb.schema import (
    REQUIRED_SECTIONS,
    load_domain_cards,
    load_risk_tag_ids,
    validate_domain_knowledge,
)


def test_phase_a_domain_cards_validate() -> None:
    report = validate_domain_knowledge()

    assert report["valid"], report["errors"]
    assert report["card_count"] >= 20
    assert report["review_status_counts"] == {"generated": report["card_count"]}
    assert "ahu.co2_control.v1" in report["card_ids"]
    assert "ahu.freeze_protection.v1" in report["card_ids"]
    assert "plant_room.chiller_sequence.v1" in report["card_ids"]
    assert "common.protection_rules.v1" in report["card_ids"]


def test_domain_cards_have_required_sections_and_no_placeholder_metadata() -> None:
    cards = load_domain_cards()
    required_sections = set(REQUIRED_SECTIONS)

    for card in cards:
        metadata = card.metadata
        assert set(card.sections) >= required_sections, card.relative_path
        assert metadata["id"].startswith(("ahu.", "plant_room.", "common.")), card.relative_path
        assert metadata["owner"] == "controls_engineering", card.relative_path
        assert metadata["review_status"] == "generated", card.relative_path
        assert metadata["source_refs"], card.relative_path
        assert "示例" not in metadata["id"], card.relative_path


def test_domain_card_risk_tags_are_defined_by_ontology() -> None:
    allowed_tags = load_risk_tag_ids()

    for card in load_domain_cards():
        for risk_tag in card.metadata["risk_tags"]:
            assert risk_tag in allowed_tags, (card.relative_path, risk_tag)


def test_ontology_files_contain_phase_a_core_concepts() -> None:
    ontology_dir = Path("knowledge/ontology")
    vocabulary = json.loads((ontology_dir / "vocabulary.json").read_text(encoding="utf-8"))
    entity_types = json.loads((ontology_dir / "entity_types.json").read_text(encoding="utf-8"))
    relationships = json.loads((ontology_dir / "relationship_types.json").read_text(encoding="utf-8"))
    risk_tags = json.loads((ontology_dir / "risk_tags.json").read_text(encoding="utf-8"))

    vocabulary_ids = {item["id"] for item in vocabulary["terms"]}
    entity_type_ids = {item["id"] for item in entity_types["entity_types"]}
    relationship_ids = {item["id"] for item in relationships["relationship_types"]}
    risk_tag_ids = {item["id"] for item in risk_tags["risk_tags"]}

    assert {"co2", "freeze_protection", "bypass_valve", "modbus", "bacnet"} <= vocabulary_ids
    assert {"subflow_definition", "subflow_instance", "configuration_model"} <= entity_type_ids
    assert {"feeds_signal_to", "references", "requires_confirmation"} <= relationship_ids
    assert {"protection_logic", "io_point_mapping", "communication_mapping"} <= risk_tag_ids


def test_invalid_domain_card_reports_missing_required_section(tmp_path: Path) -> None:
    domain_dir = tmp_path / "domain"
    ontology_dir = tmp_path / "ontology"
    domain_dir.mkdir()
    ontology_dir.mkdir()
    (ontology_dir / "risk_tags.json").write_text(
        json.dumps(
            {
                "schema_version": "test",
                "risk_tags": [{"id": "protection_logic", "label": "保护逻辑"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (domain_dir / "invalid.md").write_text(
        """---
id: common.invalid.v1
title: 无效知识卡
card_type: risk_card
project_type: common
equipment: [保护信号]
function_type: invalid
question_patterns: [能不能删除保护]
applies_when: [涉及保护]
avoid_when: [缺少人工确认]
risk_tags: [protection_logic]
answer_type: risk_review
evidence_requirement: [当前工程保护链]
patchability: blocked
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/工程规范.md#不可默认删除的逻辑]
---

## 结论

不能删除。
""",
        encoding="utf-8",
    )

    report = validate_domain_knowledge(domain_dir=domain_dir, ontology_dir=ontology_dir)

    assert not report["valid"]
    assert any("缺少正文章节: 适用前提" in error["message"] for error in report["errors"])
