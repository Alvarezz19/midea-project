from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.json_project import get_project_version, load_project
from app.services.patch_engine import build_node_diff


class ProjectDiffError(ValueError):
    """工程版本 diff 生成失败。"""


def diff_project_versions(
    project_id: str,
    from_version_id: str,
    to_version_id: str,
    *,
    versions_dir: str | Path,
) -> dict[str, Any]:
    """对比同一项目的两个不可变版本，返回节点级 diff 摘要。"""

    if from_version_id == to_version_id:
        raise ProjectDiffError("from_version_id 和 to_version_id 不能相同。")

    try:
        from_metadata = get_project_version(project_id, from_version_id, versions_dir=versions_dir)
        to_metadata = get_project_version(project_id, to_version_id, versions_dir=versions_dir)
    except ValueError as exc:
        raise ProjectDiffError(str(exc)) from exc

    from_nodes = load_project(str(from_metadata["version_path"]))
    to_nodes = load_project(str(to_metadata["version_path"]))
    return {
        "project_id": project_id,
        "from_version": _public_version_metadata(from_metadata),
        "to_version": _public_version_metadata(to_metadata),
        "diff": build_node_diff(from_nodes, to_nodes),
    }


def _public_version_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_id": metadata.get("version_id"),
        "parent_version_id": metadata.get("parent_version_id"),
        "version_path": metadata.get("version_path"),
        "json_sha256": metadata.get("json_sha256"),
        "exportable": metadata.get("exportable"),
        "created_at": metadata.get("created_at"),
    }
