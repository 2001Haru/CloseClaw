"""Factory for constructing LLM providers."""

from __future__ import annotations

import logging
from typing import Any

from .litellm_provider import LiteLLMProvider
from .openai_compatible import OpenAICompatibleProvider
from .registry import find_provider_spec

logger = logging.getLogger(__name__)


def _resolve_base_url(provider: str, base_url: str) -> str:
    if base_url:
        return base_url

    default_urls = {
        "openai": "https://api.openai.com/v1",
        "ohmygpt": "https://api.ohmygpt.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "ollama": "http://localhost:11434/v1",
        "openai-compatible": "https://api.openai.com/v1",
    }
    return default_urls.get(provider.lower(), "https://api.openai.com/v1")


def create_llm_provider(
    provider: str,
    model: str,
    api_key: str = "",
    base_url: str = "",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    timeout_seconds: int = 60,
    **kwargs: Any,
) -> Any:
    """Create provider instance based on explicit provider and model hints."""
    spec = find_provider_spec(provider, model)

    if spec.runtime == "litellm":
        logger.info("Using LiteLLM provider runtime for provider=%s model=%s", spec.name, model)
        return LiteLLMProvider(
            api_key=api_key or "",
            model=model,
            provider=spec.name,
            api_base=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    resolved_url = _resolve_base_url(provider or spec.name, base_url)
    if not api_key and (provider or spec.name).lower() != "ollama":
        logger.warning(
            "No API key provided for provider '%s'. Set api_key in config or env var.",
            provider or spec.name,
        )

    return OpenAICompatibleProvider(
        api_key=api_key or "",
        model=model,
        base_url=resolved_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
