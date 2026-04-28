from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings, get_settings


ProviderName = Literal["deepseek", "openai", "azure_openai", "anthropic"]


class LLMGatewayError(RuntimeError):
    """LLM 调用失败。"""


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: ProviderName
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    temperature: float
    max_tokens: int


def get_provider_config(provider: str | None = None, *, settings: Settings | None = None) -> LLMProviderConfig:
    settings = settings or get_settings()
    selected = (provider or settings.llm_provider).lower()
    if selected == "deepseek":
        return LLMProviderConfig(
            provider="deepseek",
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
    if selected == "openai":
        return LLMProviderConfig(
            provider="openai",
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
    if selected == "azure_openai":
        return LLMProviderConfig(
            provider="azure_openai",
            api_key=settings.azure_openai_api_key,
            base_url=settings.azure_openai_endpoint,
            model=settings.azure_openai_deployment,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
    if selected == "anthropic":
        return LLMProviderConfig(
            provider="anthropic",
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            model=settings.anthropic_model,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
    raise LLMGatewayError(f"不支持的 LLM provider: {selected}")


def chat_json(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    settings: Settings | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON 对象输出。"""

    config = get_provider_config(provider, settings=settings)
    if not config.api_key:
        raise LLMGatewayError(f"{config.provider} API key 未配置。")
    if not config.base_url:
        raise LLMGatewayError(f"{config.provider} base_url 未配置。")
    if not config.model and not model:
        raise LLMGatewayError(f"{config.provider} model 未配置。")

    if config.provider == "anthropic":
        raise LLMGatewayError("Anthropic provider 配置入口已预留，当前阶段尚未接入 Messages API。")

    started_at = time.perf_counter()
    payload = _build_openai_compatible_payload(
        messages,
        provider=config.provider,
        model=model or config.model,
        temperature=config.temperature if temperature is None else temperature,
        max_tokens=config.max_tokens if max_tokens is None else max_tokens,
    )
    raw = _post_json(
        _chat_completions_url(config),
        payload,
        api_key=config.api_key,
        provider=config.provider,
        timeout=config.timeout_seconds,
    )
    content = _extract_openai_compatible_content(raw)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMGatewayError(f"LLM 未返回合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMGatewayError("LLM JSON 输出根节点必须是对象。")
    parsed["_llm_meta"] = {
        "provider": config.provider,
        "model": model or config.model,
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
    }
    return parsed


def _build_openai_compatible_payload(
    messages: list[dict[str, str]],
    *,
    provider: ProviderName,
    model: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    _validate_messages(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    if provider == "deepseek" and model.startswith("deepseek-v4"):
        # DeepSeek v4 默认开启 thinking。结构化抽取使用非思考模式更稳定。
        payload["thinking"] = {"type": "disabled"}
    return payload


def _validate_messages(messages: list[dict[str, str]]) -> None:
    if not messages:
        raise LLMGatewayError("messages 不能为空。")
    for index, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise LLMGatewayError(f"第 {index} 条 message role 无效。")
        if not isinstance(content, str) or not content.strip():
            raise LLMGatewayError(f"第 {index} 条 message content 不能为空。")


def _chat_completions_url(config: LLMProviderConfig) -> str:
    if config.provider == "azure_openai":
        return (
            f"{config.base_url}/openai/deployments/{config.model}/chat/completions"
            "?api-version=2024-10-21"
        )
    return f"{config.base_url}/chat/completions"


def _post_json(url: str, payload: dict[str, Any], *, api_key: str, provider: ProviderName, timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if provider == "azure_openai":
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMGatewayError(f"LLM HTTP {exc.code}: {_truncate(detail)}") from exc
    except urllib.error.URLError as exc:
        raise LLMGatewayError(f"LLM 网络请求失败: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMGatewayError("LLM 请求超时。") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMGatewayError(f"LLM 响应不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMGatewayError("LLM 响应根节点必须是对象。")
    return parsed


def _extract_openai_compatible_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMGatewayError("LLM 响应缺少 choices。")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMGatewayError("LLM choices[0] 必须是对象。")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMGatewayError("LLM choices[0].message 必须是对象。")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMGatewayError("LLM 响应 content 为空。")
    return content


def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
