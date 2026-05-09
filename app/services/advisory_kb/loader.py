from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.json_project import ROOT_DIR


ADVISORY_INDEX_DIR = ROOT_DIR / "indexes" / "advisory"
MANIFEST_PATH = ADVISORY_INDEX_DIR / "advisory_kb_manifest.json"

INDEX_FILE_NAMES = {
    "template_profile": "template_profile_index.jsonl",
    "tab_role": "tab_role_index.jsonl",
    "point_identity": "point_identity_index.jsonl",
    "quote_reference": "quote_reference_index.jsonl",
    "point_usage": "point_usage_index.jsonl",
    "subflow": "subflow_index.jsonl",
    "configuration_model": "configuration_model_index.jsonl",
    "bitmask_mapping": "bitmask_mapping_index.jsonl",
    "algorithm_role": "algorithm_role_index.jsonl",
    "control_chain": "control_chain_index.jsonl",
    "protection_chain": "protection_chain_index.jsonl",
    "parameter_stat": "parameter_stat_index.jsonl",
    "code_facts": "code_facts.jsonl",
}


class AdvisoryKbLoadError(ValueError):
    """咨询知识库索引读取失败。"""


@lru_cache(maxsize=1)
def load_manifest(path: str | Path = MANIFEST_PATH) -> dict[str, Any]:
    target = _resolve_path(path)
    if not target.exists():
        raise AdvisoryKbLoadError(f"咨询知识库 manifest 不存在: {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AdvisoryKbLoadError(f"咨询知识库 manifest 必须是对象: {target}")
    return data


@lru_cache(maxsize=32)
def load_index(name: str, index_dir: str | Path = ADVISORY_INDEX_DIR) -> list[dict[str, Any]]:
    file_name = INDEX_FILE_NAMES.get(name)
    if file_name is None:
        raise AdvisoryKbLoadError(f"未知咨询知识库索引: {name}")
    base_dir = _resolve_path(index_dir)
    path = base_dir / file_name
    if not path.exists():
        raise AdvisoryKbLoadError(f"咨询知识库索引不存在: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            value = json.loads(stripped)
            if not isinstance(value, dict):
                raise AdvisoryKbLoadError(f"{path}:{line_number} 索引行必须是对象")
            rows.append(value)
    return rows


def _resolve_path(path: str | Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    return target
