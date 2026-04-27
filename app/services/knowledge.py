from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.json_project import ROOT_DIR
from app.services.retrieval import text_score, tokenize


KNOWLEDGE_DIR = ROOT_DIR / "knowledge"


class KnowledgeError(ValueError):
    """知识库读取或检索失败。"""


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    source_path: str
    title: str
    heading_level: int
    content: str

    def to_dict(self, score: float | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "source_path": self.source_path,
            "title": self.title,
            "heading_level": self.heading_level,
            "content": self.content,
        }
        if score is not None:
            data["score"] = round(score, 4)
        return data


def load_knowledge_chunks(knowledge_dir: str | Path = KNOWLEDGE_DIR) -> list[KnowledgeChunk]:
    base_dir = _resolve_knowledge_dir(knowledge_dir)
    chunks: list[KnowledgeChunk] = []
    for path in sorted(base_dir.glob("*.md")):
        chunks.extend(parse_markdown_knowledge(path))
    return chunks


def parse_markdown_knowledge(path: str | Path) -> list[KnowledgeChunk]:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    if not target.exists():
        raise KnowledgeError(f"知识文件不存在: {target}")

    relative_path = target.relative_to(ROOT_DIR).as_posix() if target.is_relative_to(ROOT_DIR) else str(target)
    lines = target.read_text(encoding="utf-8").splitlines()
    document_title = target.stem
    current_title = document_title
    current_level = 1
    current_lines: list[str] = []
    chunks: list[KnowledgeChunk] = []
    section_index = 0

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            if current_lines:
                chunk = _make_chunk(relative_path, current_title, current_level, current_lines, section_index)
                chunks.append(chunk)
                section_index += 1
            current_level = len(heading.group(1))
            current_title = heading.group(2).strip()
            current_lines = []
            continue
        current_lines.append(line)

    if current_lines:
        chunks.append(_make_chunk(relative_path, current_title, current_level, current_lines, section_index))

    return [chunk for chunk in chunks if chunk.content.strip()]


def search_knowledge(
    query: str,
    *,
    limit: int = 5,
    source_contains: str | None = None,
    min_score: float = 0.1,
    chunks: list[KnowledgeChunk] | None = None,
) -> list[dict[str, Any]]:
    if chunks is None:
        chunks = load_knowledge_chunks()
    query_tokens = tokenize(query)
    results: list[dict[str, Any]] = []

    for chunk in chunks:
        if source_contains and source_contains not in chunk.source_path:
            continue
        text = f"{chunk.source_path} {chunk.title} {chunk.content}"
        score = text_score(text, query_tokens)
        if score < min_score:
            continue
        results.append(chunk.to_dict(score))

    results.sort(key=lambda item: (item["score"], item["source_path"], item["title"]), reverse=True)
    return results[:limit]


def get_knowledge_context(query: str, *, limit: int = 3, max_chars: int = 1800) -> str:
    """返回可直接放入 LLM 上下文的知识片段文本。"""

    items = search_knowledge(query, limit=limit)
    parts: list[str] = []
    current_length = 0
    for item in items:
        block = f"来源：{item['source_path']} / {item['title']}\n{item['content']}".strip()
        if current_length + len(block) > max_chars and parts:
            break
        parts.append(block)
        current_length += len(block)
    return "\n\n---\n\n".join(parts)


def _make_chunk(source_path: str, title: str, heading_level: int, lines: list[str], section_index: int) -> KnowledgeChunk:
    content = "\n".join(lines).strip()
    safe_title = re.sub(r"\s+", "-", title.strip())
    return KnowledgeChunk(
        chunk_id=f"{source_path}#{section_index}-{safe_title}",
        source_path=source_path,
        title=title,
        heading_level=heading_level,
        content=content,
    )


def _resolve_knowledge_dir(path: str | Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    if not target.exists() or not target.is_dir():
        raise KnowledgeError(f"知识库目录不存在: {target}")
    return target
