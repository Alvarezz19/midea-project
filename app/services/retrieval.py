from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path
from typing import Any

from app.services.json_project import ROOT_DIR, load_project


INDEXES_DIR = ROOT_DIR / "indexes"
TEMPLATE_INDEX_PATH = INDEXES_DIR / "templates" / "template_index.json"
TAB_INDEX_PATH = INDEXES_DIR / "tabs" / "tab_index.jsonl"
NODE_INDEX_PATH = INDEXES_DIR / "nodes" / "node_index.jsonl"
BLOCK_INDEX_PATH = INDEXES_DIR / "blocks" / "block_index.jsonl"

PROJECT_TYPE_ALIASES = {
    "plant_room": "plant_room",
    "机房": "plant_room",
    "机房群控": "plant_room",
    "群控": "plant_room",
    "冷站": "plant_room",
    "ahu": "ahu",
    "AHU": "ahu",
    "空调箱": "ahu",
    "空气处理机组": "ahu",
    "新风机组": "ahu",
}

FEATURE_KEYWORDS = {
    "has_pump_control": ["水泵", "冷冻泵", "冷却泵", "泵"],
    "has_valve_control": ["阀", "蝶阀", "水阀", "阀门"],
    "has_bypass_valve": ["旁通", "旁通阀"],
    "has_cooling_tower": ["冷却塔"],
    "has_air_source_heat_pump": ["风冷热泵", "风冷", "热泵"],
    "has_ahu": ["AHU", "ahu", "空调箱", "空气处理机组", "新风机组"],
    "has_exhaust_fan": ["排风", "排风机"],
    "has_dx_unit": ["直膨", "直膨机"],
    "has_modbus": ["Modbus", "modbus", "通讯"],
    "has_bacnet": ["BACnet", "BACnet/IP", "BACIP"],
    "has_mqtt": ["MQTT", "mqtt"],
    "has_hard_io": ["硬接", "IO", "物理输入", "物理输出"],
}


class RetrievalError(ValueError):
    """索引检索失败。"""


def load_template_index(path: str | Path = TEMPLATE_INDEX_PATH) -> list[dict[str, Any]]:
    return _load_json_array(path)


def load_tab_index(path: str | Path = TAB_INDEX_PATH) -> list[dict[str, Any]]:
    return _load_jsonl(path)


def load_node_index(path: str | Path = NODE_INDEX_PATH) -> list[dict[str, Any]]:
    return _load_jsonl(path)


def load_block_index(path: str | Path = BLOCK_INDEX_PATH) -> list[dict[str, Any]]:
    return _load_jsonl(path)


def search_templates(
    query: str,
    *,
    project_type: str | None = None,
    limit: int = 3,
    min_score: float = 0.0,
    templates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """基于索引检索候选模板，返回带 score 和 reasons 的结果。"""

    if templates is None:
        templates = load_template_index()
    normalized_type = normalize_project_type(project_type or query)
    query_tokens = tokenize(query)
    feature_hits = infer_feature_hits(query)

    results: list[dict[str, Any]] = []
    for template in templates:
        if normalized_type and template.get("project_type") != normalized_type:
            continue
        score, reasons = score_template(template, query, query_tokens, feature_hits, normalized_type)
        if score < min_score:
            continue
        item = dict(template)
        item["score"] = round(score, 4)
        item["reasons"] = reasons
        results.append(item)

    results.sort(key=lambda item: (item["score"], item.get("node_count", 0)), reverse=True)
    return results[:limit]


def get_template_by_id(template_id: str, templates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if templates is None:
        templates = load_template_index()
    for template in templates:
        if template.get("template_id") == template_id:
            return template
    raise RetrievalError(f"模板不存在: {template_id}")


def search_tabs(
    query: str,
    *,
    template_id: str | None = None,
    limit: int = 5,
    tabs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if tabs is None:
        tabs = load_tab_index()
    query_tokens = tokenize(query)
    results: list[dict[str, Any]] = []
    for tab in tabs:
        if template_id and tab.get("template_id") != template_id:
            continue
        text = f"{tab.get('tab_label', '')} {tab.get('summary', '')} {json.dumps(tab.get('node_type_counts', {}), ensure_ascii=False)}"
        score = text_score(text, query_tokens)
        if score <= 0:
            continue
        item = dict(tab)
        item["score"] = round(score, 4)
        results.append(item)
    results.sort(key=lambda item: (item["score"], item.get("node_count", 0)), reverse=True)
    return results[:limit]


def search_nodes(
    query: str,
    *,
    template_id: str | None = None,
    tab_label_contains: str | None = None,
    node_type: str | None = None,
    limit: int = 10,
    nodes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if nodes is None:
        nodes = load_node_index()
    query_tokens = tokenize(query)
    results: list[dict[str, Any]] = []
    for node in nodes:
        if template_id and node.get("template_id") != template_id:
            continue
        if tab_label_contains and tab_label_contains not in str(node.get("tab_label", "")):
            continue
        if node_type and node.get("type") != node_type:
            continue
        text = f"{node.get('text_for_search', '')} {json.dumps(node.get('key_params', {}), ensure_ascii=False)}"
        score = text_score(text, query_tokens)
        if node_type and node.get("type") == node_type:
            score += 0.5
        if score <= 0:
            continue
        item = dict(node)
        item["score"] = round(score, 4)
        results.append(item)
    results.sort(key=lambda item: (item["score"], len(_node_input_sources(item))), reverse=True)
    return results[:limit]


def search_blocks(
    query: str,
    *,
    template_id: str | None = None,
    project_type: str | None = None,
    function_type: str | None = None,
    limit: int = 8,
    min_score: float = 0.0,
    blocks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if blocks is None:
        blocks = load_block_index()
    query_tokens = tokenize(query)
    normalized_type = normalize_project_type(project_type)
    results: list[dict[str, Any]] = []
    for block in blocks:
        if template_id and block.get("template_id") != template_id:
            continue
        if normalized_type and block.get("project_type") != normalized_type:
            continue
        if function_type and block.get("function_type") != function_type:
            continue
        score, reasons = score_block(block, query_tokens)
        if score <= 0:
            continue
        if score < min_score:
            continue
        item = dict(block)
        item["score"] = round(score, 4)
        item["reasons"] = reasons
        results.append(item)
    results.sort(key=lambda item: (item["score"], -int(item.get("node_count", 0))), reverse=True)
    return results[:limit]


def get_block_by_id(block_id: str, blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if blocks is None:
        blocks = load_block_index()
    for block in blocks:
        if block.get("block_id") == block_id:
            return block
    raise RetrievalError(f"功能块不存在: {block_id}")


def load_block_context(
    block_id: str,
    *,
    max_nodes: int = 80,
    max_chars: int = 16000,
    include_raw_nodes: bool = False,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """按 block_id 加载局部 JSON 上下文，避免向 LLM 传入完整工程。"""

    if max_nodes < 1:
        raise RetrievalError("max_nodes 必须大于 0。")
    if max_chars < 1000:
        raise RetrievalError("max_chars 不能小于 1000。")

    block = get_block_by_id(block_id, blocks=blocks)
    source_path = block.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        raise RetrievalError(f"功能块缺少 source_path: {block_id}")
    block_node_ids = block.get("node_ids")
    if not isinstance(block_node_ids, list) or not all(isinstance(node_id, str) for node_id in block_node_ids):
        raise RetrievalError(f"功能块 node_ids 无效: {block_id}")

    all_nodes = load_project(source_path)
    node_by_id = {node.get("id"): node for node in all_nodes if isinstance(node.get("id"), str)}
    selected_ids = [node_id for node_id in block_node_ids if node_id in node_by_id]
    missing_ids = [node_id for node_id in block_node_ids if node_id not in node_by_id]
    truncated_by_node_count = len(selected_ids) > max_nodes
    selected_ids = selected_ids[:max_nodes]
    selected_set = set(selected_ids)

    upstream_by_node, downstream_by_node = _build_directional_edges(node_by_id)
    context_nodes = [
        _context_node(node_by_id[node_id], include_raw=include_raw_nodes)
        for node_id in selected_ids
    ]
    internal_edges = [
        {"source": source_id, "target": target_id}
        for target_id in selected_ids
        for source_id in sorted(upstream_by_node.get(target_id, set()))
        if source_id in selected_set
    ]
    inbound_edges = [
        {"source": source_id, "target": target_id}
        for target_id in selected_ids
        for source_id in sorted(upstream_by_node.get(target_id, set()))
        if source_id not in selected_set
    ]
    outbound_edges = [
        {"source": source_id, "target": target_id}
        for source_id in selected_ids
        for target_id in sorted(downstream_by_node.get(source_id, set()))
        if target_id not in selected_set
    ]

    result = {
        "block": _public_block_metadata(block),
        "source_path": source_path,
        "nodes": context_nodes,
        "internal_edges": internal_edges,
        "boundary": {
            "entry_node_ids": block.get("entry_node_ids", []),
            "exit_node_ids": block.get("exit_node_ids", []),
            "inbound_edges": inbound_edges[:40],
            "outbound_edges": outbound_edges[:40],
            "truncated_inbound_edges": len(inbound_edges) > 40,
            "truncated_outbound_edges": len(outbound_edges) > 40,
        },
        "missing_node_ids": missing_ids,
        "budget": {
            "max_nodes": max_nodes,
            "max_chars": max_chars,
            "node_count": len(context_nodes),
            "internal_edge_count": len(internal_edges),
            "estimated_chars": 0,
            "truncated_by_node_count": truncated_by_node_count,
            "truncated_by_chars": False,
        },
    }
    return _enforce_context_char_budget(result, max_chars=max_chars)


def score_block(block: dict[str, Any], query_tokens: list[str]) -> tuple[float, list[str]]:
    text = " ".join(
        [
            str(block.get("title", "")),
            str(block.get("summary", "")),
            str(block.get("tab_label", "")),
            str(block.get("function_type", "")),
            str(block.get("text_for_search", "")),
            " ".join(str(tag) for tag in block.get("risk_tags", [])),
            json.dumps(block.get("node_type_counts", {}), ensure_ascii=False),
        ]
    )
    lexical_score = text_score(text, query_tokens)
    score = lexical_score
    reasons: list[str] = []
    if lexical_score:
        reasons.append(f"功能块关键词匹配得分：{round(lexical_score, 2)}")
    if block.get("function_type") != "tab_overview":
        if lexical_score:
            score += 0.5
            reasons.append(f"功能类型匹配：{block.get('function_type')}")
    if block.get("anchor_node_ids"):
        if lexical_score:
            score += min(len(block["anchor_node_ids"]), 5) * 0.1
    return score, reasons


def load_node_neighborhood(
    project_path: str | Path,
    anchor_node_ids: list[str],
    *,
    depth: int = 1,
    max_nodes: int = 80,
) -> dict[str, Any]:
    """从完整工程中按节点 id 加载局部邻域上下文。"""

    if depth < 0:
        raise RetrievalError("depth 不能小于 0。")
    nodes = load_project(project_path)
    node_by_id = {node.get("id"): node for node in nodes if isinstance(node.get("id"), str)}
    missing = [node_id for node_id in anchor_node_ids if node_id not in node_by_id]
    if missing:
        raise RetrievalError(f"锚点节点不存在: {missing}")

    upstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    downstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for node_id, node in node_by_id.items():
        for source_id in extract_input_source_ids(node.get("wires")):
            if source_id in node_by_id:
                upstream_by_node[node_id].add(source_id)
                downstream_by_node[source_id].add(node_id)

    selected: set[str] = set(anchor_node_ids)
    queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id in anchor_node_ids)
    while queue and len(selected) < max_nodes:
        node_id, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        neighbors = sorted(upstream_by_node[node_id] | downstream_by_node[node_id])
        for neighbor in neighbors:
            if neighbor in selected:
                continue
            selected.add(neighbor)
            queue.append((neighbor, current_depth + 1))
            if len(selected) >= max_nodes:
                break

    selected_nodes = [summarize_node_for_context(node_by_id[node_id]) for node_id in sorted(selected)]
    selected_edges = [
        {"source": source, "target": target}
        for target in sorted(selected)
        for source in sorted(upstream_by_node[target])
        if source in selected
    ]
    return {
        "project_path": str(project_path),
        "anchor_node_ids": anchor_node_ids,
        "depth": depth,
        "node_count": len(selected_nodes),
        "edge_count": len(selected_edges),
        "nodes": selected_nodes,
        "edges": selected_edges,
        "truncated": len(selected_nodes) >= max_nodes,
    }


def normalize_project_type(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    for keyword, normalized in PROJECT_TYPE_ALIASES.items():
        if keyword in text:
            return normalized
    return None


def infer_feature_hits(query: str) -> set[str]:
    hits: set[str] = set()
    for feature, keywords in FEATURE_KEYWORDS.items():
        if any(keyword in query for keyword in keywords):
            hits.add(feature)
    return hits


def score_template(
    template: dict[str, Any],
    query: str,
    query_tokens: list[str],
    feature_hits: set[str],
    project_type: str | None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if project_type and template.get("project_type") == project_type:
        score += 5.0
        reasons.append(f"项目类型匹配：{template.get('project_type_label')}")

    features = template.get("features", {})
    for feature in sorted(feature_hits):
        if features.get(feature):
            score += 2.0
            reasons.append(f"功能特征匹配：{feature}")

    text = " ".join(
        [
            str(template.get("file_name", "")),
            str(template.get("summary", "")),
            " ".join(str(tab) for tab in template.get("tabs", [])),
            json.dumps(template.get("node_type_counts", {}), ensure_ascii=False),
        ]
    )
    lexical_score = text_score(text, query_tokens)
    if lexical_score:
        score += lexical_score
        reasons.append(f"关键词匹配得分：{round(lexical_score, 2)}")

    if not reasons and query.strip():
        reasons.append("未命中明确特征，仅按默认排序返回。")
    return score, reasons


def text_score(text: str, query_tokens: list[str]) -> float:
    if not query_tokens:
        return 0.0
    normalized_text = text.casefold()
    score = 0.0
    for token in query_tokens:
        normalized_token = token.casefold()
        count = normalized_text.count(normalized_token)
        if count:
            score += 1.0 + min(count, 5) * 0.2
    return score


def tokenize(text: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_./+-]+|[\u4e00-\u9fff]{2,}", text)
    tokens: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        token = token.strip()
        if not token:
            continue
        folded = token.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        tokens.append(token)
    for feature, keywords in FEATURE_KEYWORDS.items():
        del feature
        for keyword in keywords:
            if keyword in text and keyword.casefold() not in seen:
                seen.add(keyword.casefold())
                tokens.append(keyword)
    return tokens


def extract_input_source_ids(wires: Any) -> list[str]:
    sources: list[str] = []
    if not isinstance(wires, list):
        return sources
    for input_sources in wires:
        if not isinstance(input_sources, list):
            continue
        for source in input_sources:
            if isinstance(source, str):
                sources.append(source)
            elif isinstance(source, dict) and isinstance(source.get("id"), str):
                sources.append(source["id"])
    return sources


def _build_directional_edges(node_by_id: dict[str, dict[str, Any]]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    upstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    downstream_by_node: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for node_id, node in node_by_id.items():
        for source_id in extract_input_source_ids(node.get("wires")):
            if source_id in node_by_id:
                upstream_by_node[node_id].add(source_id)
                downstream_by_node[source_id].add(node_id)
    return upstream_by_node, downstream_by_node


def _public_block_metadata(block: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "block_id",
        "template_id",
        "project_type",
        "tab_id",
        "tab_label",
        "function_type",
        "title",
        "summary",
        "anchor_node_ids",
        "entry_node_ids",
        "exit_node_ids",
        "node_count",
        "node_type_counts",
        "risk_tags",
    ]
    return {key: block.get(key) for key in keys if key in block}


def _context_node(node: dict[str, Any], *, include_raw: bool) -> dict[str, Any]:
    summary = summarize_node_for_context(node)
    if include_raw:
        summary["raw"] = node
    return summary


def _enforce_context_char_budget(context: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    estimated_chars = len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))
    context["budget"]["estimated_chars"] = estimated_chars
    if estimated_chars <= max_chars:
        return context

    nodes = context["nodes"]
    while nodes and estimated_chars > max_chars:
        nodes.pop()
        context["budget"]["truncated_by_chars"] = True
        context["budget"]["node_count"] = len(nodes)
        selected_ids = {node.get("id") for node in nodes}
        context["internal_edges"] = [
            edge
            for edge in context["internal_edges"]
            if edge.get("source") in selected_ids and edge.get("target") in selected_ids
        ]
        estimated_chars = len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))
        context["budget"]["estimated_chars"] = estimated_chars
    return context


def _node_input_sources(node_index: dict[str, Any]) -> list[Any]:
    input_sources = node_index.get("input_sources")
    if isinstance(input_sources, list):
        return input_sources
    legacy_wires_to = node_index.get("wires_to")
    if isinstance(legacy_wires_to, list):
        return legacy_wires_to
    return []


def summarize_node_for_context(node: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "type",
        "z",
        "name",
        "label",
        "inputs",
        "outputs",
        "x",
        "y",
        "wires",
        "fixedValue",
        "outOfServiceValue",
        "tripPoint",
        "as",
    ]
    return {key: node[key] for key in keys if key in node}


def _load_json_array(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    with target.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise RetrievalError(f"索引文件必须是数组: {target}")
    if any(not isinstance(item, dict) for item in data):
        raise RetrievalError(f"索引数组项必须是对象: {target}")
    return data


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RetrievalError(f"JSONL 第 {line_number} 行必须是对象: {target}")
            rows.append(row)
    return rows
