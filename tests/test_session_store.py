from __future__ import annotations

from pathlib import Path

import pytest

from app.graph.state import initial_state
from app.services.session_store import FileSessionStore, SessionStoreError


def test_file_session_store_persists_state(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    state = initial_state("我要做 AHU 程序", project_type="ahu")
    state["current_project_id"] = "project_test"

    store.save("thread-1", state)
    reloaded = FileSessionStore(tmp_path / "sessions").get("thread-1")

    assert reloaded is not None
    assert reloaded["project_type"] == "ahu"
    assert reloaded["current_project_id"] == "project_test"
    assert [item["current_project_id"] for item in store.find_by_project_id("project_test")] == ["project_test"]
    assert [(thread_id, state["current_project_id"]) for thread_id, state in store.find_items_by_project_id("project_test")] == [
        ("thread-1", "project_test")
    ]


def test_file_session_store_rejects_path_like_thread_id(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")

    with pytest.raises(SessionStoreError, match="非法路径字符"):
        store.get("../bad")
