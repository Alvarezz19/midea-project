from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.json_project import ROOT_DIR


ONTOLOGY_DIR = ROOT_DIR / "knowledge" / "ontology"
DOMAIN_DIR = ROOT_DIR / "knowledge" / "domain"

REQUIRED_METADATA_FIELDS = {
    "id",
    "title",
    "card_type",
    "project_type",
    "equipment",
    "function_type",
    "question_patterns",
    "applies_when",
    "avoid_when",
    "risk_tags",
    "answer_type",
    "evidence_requirement",
    "patchability",
    "review_status",
    "owner",
    "updated_at",
    "source_refs",
}

LIST_METADATA_FIELDS = {
    "equipment",
    "question_patterns",
    "applies_when",
    "avoid_when",
    "risk_tags",
    "evidence_requirement",
    "source_refs",
}

REQUIRED_SECTIONS = [
    "结论",
    "适用前提",
    "推荐范围",
    "控制链要求",
    "风险和禁止项",
    "需要追问的信息",
    "可转补丁条件",
    "调试验收检查",
]

ALLOWED_CARD_TYPES = {
    "strategy_card",
    "parameter_card",
    "risk_card",
    "point_card",
    "commissioning_card",
    "patchability_card",
    "case_card",
}

ALLOWED_PROJECT_TYPES = {"ahu", "plant_room", "common"}
ALLOWED_REVIEW_STATUSES = {"generated", "reviewed", "deprecated"}
ALLOWED_PATCHABILITY = {
    "not_applicable",
    "draftable",
    "needs_clarification",
    "requires_manual_design",
    "blocked",
}


class AdvisoryKnowledgeSchemaError(ValueError):
    """咨询知识库 schema 或知识卡结构无效。"""


@dataclass(frozen=True)
class DomainKnowledgeCard:
    path: Path
    relative_path: str
    metadata: dict[str, Any]
    body: str
    sections: dict[str, str]

    @property
    def card_id(self) -> str:
        return str(self.metadata.get("id") or "")

    @property
    def review_status(self) -> str:
        return str(self.metadata.get("review_status") or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "metadata": self.metadata,
            "sections": self.sections,
        }


def load_domain_cards(domain_dir: str | Path = DOMAIN_DIR) -> list[DomainKnowledgeCard]:
    base_dir = _resolve_dir(domain_dir)
    cards: list[DomainKnowledgeCard] = []
    for path in sorted(base_dir.rglob("*.md")):
        if "templates" in path.relative_to(base_dir).parts or path.name.endswith(".template.md"):
            continue
        cards.append(parse_domain_card(path))
    return cards


def parse_domain_card(path: str | Path) -> DomainKnowledgeCard:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    if not target.exists():
        raise AdvisoryKnowledgeSchemaError(f"知识卡不存在: {target}")

    text = target.read_text(encoding="utf-8")
    metadata, body = _split_front_matter(text, target)
    relative_path = target.relative_to(ROOT_DIR).as_posix() if target.is_relative_to(ROOT_DIR) else str(target)
    return DomainKnowledgeCard(
        path=target,
        relative_path=relative_path,
        metadata=metadata,
        body=body,
        sections=_extract_sections(body),
    )


def validate_domain_knowledge(
    *,
    domain_dir: str | Path = DOMAIN_DIR,
    ontology_dir: str | Path = ONTOLOGY_DIR,
) -> dict[str, Any]:
    cards = load_domain_cards(domain_dir)
    risk_tag_ids = load_risk_tag_ids(ontology_dir)
    errors: list[dict[str, str]] = []
    card_ids: set[str] = set()
    review_status_counts: dict[str, int] = {}

    for card in cards:
        card_errors = validate_domain_card(card, risk_tag_ids=risk_tag_ids)
        if card.card_id in card_ids:
            card_errors.append(f"id 重复: {card.card_id}")
        if card.card_id:
            card_ids.add(card.card_id)

        review_status = card.review_status
        if review_status:
            review_status_counts[review_status] = review_status_counts.get(review_status, 0) + 1

        for message in card_errors:
            errors.append({"path": card.relative_path, "message": message})

    if not cards:
        errors.append({"path": str(Path(domain_dir)), "message": "没有找到领域知识卡"})

    return {
        "valid": not errors,
        "card_count": len(cards),
        "review_status_counts": review_status_counts,
        "card_ids": sorted(card_ids),
        "errors": errors,
    }


def validate_domain_card(card: DomainKnowledgeCard, *, risk_tag_ids: set[str] | None = None) -> list[str]:
    metadata = card.metadata
    errors: list[str] = []

    for field in sorted(REQUIRED_METADATA_FIELDS):
        if field not in metadata:
            errors.append(f"缺少 front matter 字段: {field}")

    card_id = str(metadata.get("id") or "")
    if card_id and not re.fullmatch(r"[a-z0-9_.-]+\.v[0-9]+", card_id):
        errors.append(f"id 格式无效: {card_id}")

    card_type = str(metadata.get("card_type") or "")
    if card_type and card_type not in ALLOWED_CARD_TYPES:
        errors.append(f"card_type 不在允许范围: {card_type}")

    project_type = str(metadata.get("project_type") or "")
    if project_type and project_type not in ALLOWED_PROJECT_TYPES:
        errors.append(f"project_type 不在允许范围: {project_type}")

    review_status = str(metadata.get("review_status") or "")
    if review_status and review_status not in ALLOWED_REVIEW_STATUSES:
        errors.append(f"review_status 不在允许范围: {review_status}")

    patchability = str(metadata.get("patchability") or "")
    if patchability and patchability not in ALLOWED_PATCHABILITY:
        errors.append(f"patchability 不在允许范围: {patchability}")

    updated_at = str(metadata.get("updated_at") or "")
    if updated_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated_at):
        errors.append(f"updated_at 必须使用 YYYY-MM-DD: {updated_at}")

    for field in sorted(LIST_METADATA_FIELDS):
        value = metadata.get(field)
        if not isinstance(value, list):
            errors.append(f"{field} 必须是列表")
        elif field != "source_refs" and not value:
            errors.append(f"{field} 不能为空")
        elif any(not str(item).strip() for item in value):
            errors.append(f"{field} 不能包含空项")

    if review_status == "reviewed" and not metadata.get("source_refs"):
        errors.append("reviewed 知识卡必须保留 source_refs")

    risk_tags = metadata.get("risk_tags")
    if isinstance(risk_tags, list) and risk_tag_ids is not None:
        for risk_tag in risk_tags:
            if str(risk_tag) not in risk_tag_ids:
                errors.append(f"risk_tags 未在 ontology 中定义: {risk_tag}")

    for section in REQUIRED_SECTIONS:
        content = card.sections.get(section)
        if content is None:
            errors.append(f"缺少正文章节: {section}")
        elif not content.strip():
            errors.append(f"正文章节为空: {section}")

    return errors


def load_risk_tag_ids(ontology_dir: str | Path = ONTOLOGY_DIR) -> set[str]:
    target_dir = _resolve_dir(ontology_dir)
    path = target_dir / "risk_tags.json"
    if not path.exists():
        raise AdvisoryKnowledgeSchemaError(f"风险标签 ontology 不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    tags = data.get("risk_tags")
    if not isinstance(tags, list):
        raise AdvisoryKnowledgeSchemaError("risk_tags.json 缺少 risk_tags 列表")

    result: set[str] = set()
    for item in tags:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise AdvisoryKnowledgeSchemaError("risk_tags.json 存在无效标签")
        result.add(item["id"])
    return result


def _split_front_matter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AdvisoryKnowledgeSchemaError(f"知识卡缺少 front matter: {path}")

    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        raise AdvisoryKnowledgeSchemaError(f"知识卡 front matter 未闭合: {path}")

    metadata = _parse_simple_front_matter(lines[1:closing_index], path)
    body = "\n".join(lines[closing_index + 1 :]).strip()
    return metadata, body


def _parse_simple_front_matter(lines: list[str], path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line_number, line in enumerate(lines, start=2):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            raise AdvisoryKnowledgeSchemaError(f"{path}:{line_number} front matter 行缺少冒号")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise AdvisoryKnowledgeSchemaError(f"{path}:{line_number} front matter 字段名为空")
        metadata[key] = _parse_front_matter_value(raw_value.strip())
    return metadata


def _parse_front_matter_value(raw_value: str) -> Any:
    if raw_value.startswith("[") and raw_value.endswith("]"):
        inner = raw_value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(item.strip()) for item in inner.split(",")]
    return _unquote(raw_value)


def _extract_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_heading: str | None = None

    for line in body.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current_heading = heading.group(1).strip()
            sections.setdefault(current_heading, [])
            continue
        if current_heading is not None:
            sections[current_heading].append(line)

    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _resolve_dir(path: str | Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    if not target.exists() or not target.is_dir():
        raise AdvisoryKnowledgeSchemaError(f"目录不存在: {target}")
    return target
