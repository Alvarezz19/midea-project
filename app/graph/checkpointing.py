from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import settings

_CHECKPOINTER: Any | None = None
_CHECKPOINTER_CONTEXT: Any | None = None


def get_checkpointer() -> Any | None:
    """返回当前配置的 LangGraph checkpointer。

    生产环境可配置 PostgreSQL 后端；默认 memory 后端用于本地开发和测试。
    """

    global _CHECKPOINTER
    backend = str(settings.langgraph_checkpointer_backend).lower()
    if backend in {"", "none", "disabled"}:
        return None
    if backend == "postgres":
        return _get_postgres_checkpointer()
    if backend != "memory":
        raise RuntimeError(f"暂不支持的 LangGraph checkpointer 后端: {backend}")
    if _CHECKPOINTER is None:
        _CHECKPOINTER = InMemorySaver()
    return _CHECKPOINTER


def reset_checkpointer() -> None:
    """关闭并重置已创建的 checkpointer，主要用于测试和进程退出清理。"""

    global _CHECKPOINTER, _CHECKPOINTER_CONTEXT
    context = _CHECKPOINTER_CONTEXT
    _CHECKPOINTER = None
    _CHECKPOINTER_CONTEXT = None
    if context is not None and hasattr(context, "__exit__"):
        context.__exit__(None, None, None)


def _get_postgres_checkpointer() -> Any:
    global _CHECKPOINTER, _CHECKPOINTER_CONTEXT
    if _CHECKPOINTER is not None:
        return _CHECKPOINTER

    database_url = str(settings.database_url or "").strip()
    if not database_url:
        raise RuntimeError("LANGGRAPH_CHECKPOINTER_BACKEND=postgres 时必须配置 DATABASE_URL。")

    postgres_saver = _load_postgres_saver()
    context = postgres_saver.from_conn_string(database_url)
    checkpointer = context.__enter__() if hasattr(context, "__enter__") else context
    if settings.langgraph_checkpointer_setup and hasattr(checkpointer, "setup"):
        checkpointer.setup()
    _CHECKPOINTER_CONTEXT = context if hasattr(context, "__exit__") else None
    _CHECKPOINTER = checkpointer
    return _CHECKPOINTER


def _load_postgres_saver() -> Any:
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL checkpointer 需要安装可选依赖：pip install -e .[postgres]"
        ) from exc
    return PostgresSaver
