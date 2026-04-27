from __future__ import annotations

import json
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
PROJECTS_DIR = ROOT_DIR / "projects"
VERSIONS_DIR = PROJECTS_DIR / "versions"


class ProjectJsonError(ValueError):
    """工程 JSON 读取或写入失败。"""


def resolve_project_path(path: str | Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT_DIR / target
    return target.resolve()


def load_project(path: str | Path) -> list[dict[str, Any]]:
    target = resolve_project_path(path)
    try:
        with target.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise ProjectJsonError(f"JSON 解析失败: {target} ({exc})") from exc
    except OSError as exc:
        raise ProjectJsonError(f"读取工程失败: {target} ({exc})") from exc

    if not isinstance(data, list):
        raise ProjectJsonError(f"工程根节点必须是数组: {target}")
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ProjectJsonError(f"工程数组第 {index} 项不是对象: {target}")
    return data


def save_project(path: str | Path, nodes: list[dict[str, Any]]) -> Path:
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise ProjectJsonError("保存工程失败：nodes 必须是对象数组。")

    target = resolve_project_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(nodes, file, ensure_ascii=False, indent=2)
            file.write("\n")
    except OSError as exc:
        raise ProjectJsonError(f"保存工程失败: {target} ({exc})") from exc
    return target


def get_tabs(nodes: list[dict[str, Any]]) -> dict[str, str]:
    tabs: dict[str, str] = {}
    for node in nodes:
        if node.get("type") != "tab":
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        label = node.get("label") or node.get("name") or node_id
        tabs[node_id] = str(label)
    return tabs


def get_node_type_counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(node.get("type", "")) for node in nodes)
    return dict(counter.most_common())


def summarize_project(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    tabs = get_tabs(nodes)
    type_counts = get_node_type_counts(nodes)
    tab_counts: dict[str, int] = {tab_id: 0 for tab_id in tabs}
    for node in nodes:
        tab_id = node.get("z")
        if isinstance(tab_id, str) and tab_id in tab_counts:
            tab_counts[tab_id] += 1

    return {
        "node_count": len(nodes),
        "tab_count": len(tabs),
        "tabs": [{"id": tab_id, "label": label, "node_count": tab_counts.get(tab_id, 0)} for tab_id, label in tabs.items()],
        "node_type_counts": type_counts,
    }


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _text_match(actual: Any, expected: Any, *, contains: bool, case_sensitive: bool) -> bool:
    actual_text = _normalize_text(actual)
    expected_text = _normalize_text(expected)
    if not case_sensitive:
        actual_text = actual_text.casefold()
        expected_text = expected_text.casefold()
    if contains:
        return expected_text in actual_text
    return actual_text == expected_text


def find_nodes(nodes: list[dict[str, Any]], selector: dict[str, Any]) -> list[dict[str, Any]]:
    """按结构化选择器查找节点。

    支持字段：id、ids、type、types、name、name_contains、tab_id、tab_label。
    默认大小写不敏感；设置 case_sensitive=true 可开启大小写敏感匹配。
    """

    if not isinstance(selector, dict):
        raise ProjectJsonError("节点选择器必须是对象。")

    tabs = get_tabs(nodes)
    case_sensitive = bool(selector.get("case_sensitive", False))
    result: list[dict[str, Any]] = []

    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        tab_id = node.get("z") if node_type != "tab" else node.get("id")
        tab_label = tabs.get(tab_id, "") if isinstance(tab_id, str) else ""

        if "id" in selector and node_id != selector["id"]:
            continue
        if "ids" in selector and node_id not in set(selector["ids"]):
            continue
        if "type" in selector and node_type != selector["type"]:
            continue
        if "types" in selector and node_type not in set(selector["types"]):
            continue
        if "name" in selector and not _text_match(node.get("name"), selector["name"], contains=False, case_sensitive=case_sensitive):
            continue
        if "name_contains" in selector and not _text_match(
            node.get("name"),
            selector["name_contains"],
            contains=True,
            case_sensitive=case_sensitive,
        ):
            continue
        if "tab_id" in selector and tab_id != selector["tab_id"]:
            continue
        if "tab_label" in selector and not _text_match(tab_label, selector["tab_label"], contains=False, case_sensitive=case_sensitive):
            continue
        if "tab_label_contains" in selector and not _text_match(
            tab_label,
            selector["tab_label_contains"],
            contains=True,
            case_sensitive=case_sensitive,
        ):
            continue
        result.append(node)
    return result


def create_project_version(
    template_path: str | Path,
    *,
    project_id: str | None = None,
    version_id: str | None = None,
    versions_dir: str | Path = VERSIONS_DIR,
    note: str = "",
) -> dict[str, Any]:
    """从模板创建工程版本，返回版本元数据。"""

    source_path = resolve_project_path(template_path)
    nodes = load_project(source_path)
    now = datetime.now(timezone.utc)
    if project_id is None:
        project_id = f"project_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    if version_id is None:
        version_id = f"v_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

    version_root = resolve_project_path(versions_dir) / project_id
    version_path = version_root / f"{version_id}.json"
    meta_path = version_root / f"{version_id}.meta.json"

    save_project(version_path, nodes)
    metadata = {
        "project_id": project_id,
        "version_id": version_id,
        "source_template_path": source_path.relative_to(ROOT_DIR).as_posix() if source_path.is_relative_to(ROOT_DIR) else str(source_path),
        "version_path": version_path.relative_to(ROOT_DIR).as_posix() if version_path.is_relative_to(ROOT_DIR) else str(version_path),
        "created_at": now.isoformat(),
        "note": note,
        "summary": summarize_project(nodes),
    }
    save_metadata(meta_path, metadata)
    return metadata


def save_metadata(path: str | Path, metadata: dict[str, Any]) -> Path:
    target = resolve_project_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return target


def copy_project_version(source_version_path: str | Path, target_version_path: str | Path) -> Path:
    source = resolve_project_path(source_version_path)
    target = resolve_project_path(target_version_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target
