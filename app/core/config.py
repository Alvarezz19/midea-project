from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def load_env_file(path: str | Path = ROOT_DIR / ".env") -> None:
    """加载本地 .env，已存在的进程环境变量优先。"""

    target = Path(path)
    if not target.exists():
        return
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


@dataclass(frozen=True)
class Settings:
    app_name: str = "Midea JSON Agent Prototype"
    app_version: str = "0.1.0"
    app_env: str = "development"
    log_level: str = "INFO"
    project_versions_dir: str = "projects/versions"
    project_sessions_dir: str = "projects/sessions"
    langgraph_checkpointer_backend: str = "memory"
    llm_provider: str = "deepseek"
    llm_timeout_seconds: float = 90.0
    llm_temperature: float = 0.1
    llm_max_tokens: int = 10000
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-4-5"


def get_settings() -> Settings:
    """从环境变量加载轻量配置，避免在原型阶段引入额外配置依赖。"""

    load_env_file()
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
        llm_provider=os.getenv("LLM_PROVIDER", Settings.llm_provider).lower(),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", Settings.llm_timeout_seconds)),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", Settings.llm_temperature)),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", Settings.llm_max_tokens)),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", Settings.deepseek_api_key),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", Settings.deepseek_base_url).rstrip("/"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", Settings.deepseek_model),
        openai_api_key=os.getenv("OPENAI_API_KEY", Settings.openai_api_key),
        openai_base_url=os.getenv("OPENAI_BASE_URL", Settings.openai_base_url).rstrip("/"),
        openai_model=os.getenv("OPENAI_MODEL", Settings.openai_model),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", Settings.azure_openai_api_key),
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", Settings.azure_openai_endpoint).rstrip("/"),
        azure_openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", Settings.azure_openai_deployment),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", Settings.anthropic_api_key),
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", Settings.anthropic_base_url).rstrip("/"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", Settings.anthropic_model),
    )


settings = get_settings()
