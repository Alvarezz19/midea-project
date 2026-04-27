from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import settings

_CHECKPOINTER: Any | None = None


def get_checkpointer() -> Any | None:
    """返回当前配置的 LangGraph checkpointer。

    生产 PostgreSQL checkpointer 需要数据库连接和额外依赖，当前先提供可测试的内存后端。
    """

    global _CHECKPOINTER
    backend = settings.langgraph_checkpointer_backend
    if backend in {"", "none", "disabled"}:
        return None
    if backend != "memory":
        raise RuntimeError(f"暂不支持的 LangGraph checkpointer 后端: {backend}")
    if _CHECKPOINTER is None:
        _CHECKPOINTER = InMemorySaver()
    return _CHECKPOINTER
