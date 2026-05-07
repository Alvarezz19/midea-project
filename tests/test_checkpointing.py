from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.graph.checkpointing as checkpointing


def setup_function() -> None:
    checkpointing.reset_checkpointer()


def teardown_function() -> None:
    checkpointing.reset_checkpointer()


def test_postgres_checkpointer_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        checkpointing,
        "settings",
        SimpleNamespace(
            langgraph_checkpointer_backend="postgres",
            database_url="",
            langgraph_checkpointer_setup=True,
        ),
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        checkpointing.get_checkpointer()


def test_postgres_checkpointer_uses_conn_string_and_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []

    class FakeCheckpointer:
        def setup(self) -> None:
            events.append(("setup", None))

    class FakeContext:
        def __enter__(self) -> FakeCheckpointer:
            events.append(("enter", None))
            return FakeCheckpointer()

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            events.append(("exit", None))

    class FakePostgresSaver:
        @classmethod
        def from_conn_string(cls, database_url: str) -> FakeContext:
            events.append(("conn", database_url))
            return FakeContext()

    monkeypatch.setattr(
        checkpointing,
        "settings",
        SimpleNamespace(
            langgraph_checkpointer_backend="postgres",
            database_url="postgresql://user:pass@localhost:5432/midea?sslmode=disable",
            langgraph_checkpointer_setup=True,
        ),
    )
    monkeypatch.setattr(checkpointing, "_load_postgres_saver", lambda: FakePostgresSaver)

    first = checkpointing.get_checkpointer()
    second = checkpointing.get_checkpointer()

    assert first is second
    assert events == [
        ("conn", "postgresql://user:pass@localhost:5432/midea?sslmode=disable"),
        ("enter", None),
        ("setup", None),
    ]

    checkpointing.reset_checkpointer()
    assert events[-1] == ("exit", None)


def test_postgres_checkpointer_can_skip_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeCheckpointer:
        def setup(self) -> None:
            events.append("setup")

    class FakePostgresSaver:
        @classmethod
        def from_conn_string(cls, database_url: str) -> FakeCheckpointer:
            events.append(database_url)
            return FakeCheckpointer()

    monkeypatch.setattr(
        checkpointing,
        "settings",
        SimpleNamespace(
            langgraph_checkpointer_backend="postgres",
            database_url="postgresql://localhost/midea",
            langgraph_checkpointer_setup=False,
        ),
    )
    monkeypatch.setattr(checkpointing, "_load_postgres_saver", lambda: FakePostgresSaver)

    checkpointing.get_checkpointer()

    assert events == ["postgresql://localhost/midea"]
