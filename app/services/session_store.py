from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.graph.state import AgentState
from app.services.json_project import ROOT_DIR, resolve_project_path
from app.services.runtime_store import postgres_runtime_enabled


class SessionStoreError(ValueError):
    """会话状态读写失败。"""


class FileSessionStore:
    """基于文件的会话状态存储。

    这是 PostgreSQL session 表接入前的本地持久化边界，接口保持简单，便于后续替换实现。
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = resolve_project_path(root_dir)

    def get(self, thread_id: str) -> AgentState | None:
        path = self._session_path(thread_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (json.JSONDecodeError, OSError) as exc:
            raise SessionStoreError(f"读取会话失败: {thread_id} ({exc})") from exc
        if not isinstance(data, dict):
            raise SessionStoreError(f"会话文件根节点必须是对象: {thread_id}")
        return data  # type: ignore[return-value]

    def save(self, thread_id: str, state: AgentState) -> None:
        path = self._session_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temp_path, path)
        except OSError as exc:
            raise SessionStoreError(f"保存会话失败: {thread_id} ({exc})") from exc

    def list_items(self) -> list[tuple[str, AgentState]]:
        if not self.root_dir.exists():
            return []
        items: list[tuple[str, AgentState]] = []
        for path in sorted(self.root_dir.glob("*.json")):
            thread_id = path.stem
            state = self.get(thread_id)
            if state is not None:
                items.append((thread_id, state))
        return items

    def list_states(self) -> list[AgentState]:
        states: list[AgentState] = []
        for _, state in self.list_items():
            states.append(state)
        return states

    def find_by_project_id(self, project_id: str) -> list[AgentState]:
        return [state for state in self.list_states() if state.get("current_project_id") == project_id]

    def find_items_by_project_id(self, project_id: str) -> list[tuple[str, AgentState]]:
        return [(thread_id, state) for thread_id, state in self.list_items() if state.get("current_project_id") == project_id]

    def _session_path(self, thread_id: str) -> Path:
        if not thread_id or any(char in thread_id for char in "\\/:*?\"<>|"):
            raise SessionStoreError("thread_id 包含非法路径字符。")
        path = (self.root_dir / f"{thread_id}.json").resolve()
        if not path.is_relative_to(self.root_dir):
            raise SessionStoreError("thread_id 解析结果越过会话目录。")
        return path


def default_session_store() -> FileSessionStore:
    return FileSessionStore(ROOT_DIR / "projects" / "sessions")


def create_session_store(root_dir: str | Path | None = None):
    if postgres_runtime_enabled():
        from app.services.postgres_runtime import PostgresSessionStore

        return PostgresSessionStore()
    return FileSessionStore(root_dir or ROOT_DIR / "projects" / "sessions")
