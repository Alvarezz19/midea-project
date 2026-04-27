from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "Midea JSON Agent Prototype"
    app_version: str = "0.1.0"
    app_env: str = "development"
    log_level: str = "INFO"
    project_versions_dir: str = "projects/versions"
    project_sessions_dir: str = "projects/sessions"
    langgraph_checkpointer_backend: str = "memory"


def get_settings() -> Settings:
    """从环境变量加载轻量配置，避免在原型阶段引入额外配置依赖。"""

    return Settings(
        app_name=os.getenv("APP_NAME", Settings.app_name),
        app_version=os.getenv("APP_VERSION", Settings.app_version),
        app_env=os.getenv("APP_ENV", Settings.app_env),
        log_level=os.getenv("LOG_LEVEL", Settings.log_level).upper(),
        project_versions_dir=os.getenv("PROJECT_VERSIONS_DIR", Settings.project_versions_dir),
        project_sessions_dir=os.getenv("PROJECT_SESSIONS_DIR", Settings.project_sessions_dir),
        langgraph_checkpointer_backend=os.getenv(
            "LANGGRAPH_CHECKPOINTER_BACKEND",
            Settings.langgraph_checkpointer_backend,
        ).lower(),
    )


settings = get_settings()
