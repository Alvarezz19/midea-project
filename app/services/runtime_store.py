from __future__ import annotations

from app.core.config import settings


def runtime_store_backend() -> str:
    return str(settings.runtime_store_backend or "file").lower()


def postgres_runtime_enabled() -> bool:
    return runtime_store_backend() == "postgres"


def require_postgres_database_url() -> str:
    database_url = str(settings.database_url or "").strip()
    if not database_url:
        raise RuntimeError("RUNTIME_STORE_BACKEND=postgres 时必须配置 DATABASE_URL。")
    return database_url
