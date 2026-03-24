"""LiteLLM-backed provider for multi-vendor support (Gemini/Anthropic/etc.)."""

from __future__ import annotations

import importlib
import logging
import time
from typing import Any, Optional

from ..types import ToolCall
from .base import (
    parse_openai_like_tool_calls,
    run_with_transient_retry,
    sanitize_empty_messages,
    sanitize_request_messages,
)

logger = logging.getLogger(__name__)
_LITELLM_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"})


class LiteLLMProvider:
    """Provider wrapper around litellm.acompletion with CloseClaw-compatible output."""

    def __init__(
        self,
        api_key: str,
        model: str,
        provider: str,
        api_base: str = "",
        temperature: float = 0.0,
        max_tokens: int = 2000,
        timeout_seconds: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

        try:
            litellm_module = importlib.import_module("litellm")
        except Exception as exc:
            raise RuntimeError(
                "litellm is required for provider '%s'. Install with: pip install litellm" % provider
            ) from exc

        self._acompletion = getattr(litellm_module, "acompletion", None)
        if not callable(self._acompletion):
            raise RuntimeError("litellm.acompletion not available; please check litellm installation")

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[str, Optional[list[ToolCall]]]:
        resolved_model = self._resolve_model(self.provider, self.model)
        cleaned_messages = sanitize_request_messages(
            sanitize_empty_messages(messages),
            _LITELLM_MSG_KEYS,
        )

        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": cleaned_messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": max(1, int(kwargs.get("max_tokens", self.max_tokens))),
        }

        if self.api_key:
            payload["api_key"] = self.api_key
        if self.api_base:
            payload["api_base"] = self.api_base

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        started = time.perf_counter()

        async def _request() -> Any:
            return await self._acompletion(**payload)

        try:
            response = await run_with_transient_retry(_request)
            text, tool_calls = self._parse_response(response)
            latency_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "provider=%s model=%s latency_ms=%.2f tool_calls=%s",
                self.provider,
                resolved_model,
                latency_ms,
                len(tool_calls or []),
            )
            return text, tool_calls
        except Exception as exc:
            raise RuntimeError(f"LiteLLM provider request failed: {exc}") from exc

    @staticmethod
    def _resolve_model(provider: str, model: str) -> str:
        provider_norm = provider.lower().strip()
        model_norm = model.strip()

        if provider_norm == "gemini" and not model_norm.startswith("gemini/"):
            return f"gemini/{model_norm}"
        if provider_norm == "anthropic" and not model_norm.startswith("anthropic/"):
            return f"anthropic/{model_norm}"
        return model_norm

    def _parse_response(self, response: Any) -> tuple[str, Optional[list[ToolCall]]]:
        # Support both LiteLLM object responses and dict-like responses.
        if hasattr(response, "choices"):
            choice = response.choices[0]
            message = choice.message
            text = getattr(message, "content", "") or ""
            raw_tool_calls = []
            if getattr(message, "tool_calls", None):
                raw_tool_calls = [
                    {
                        "id": getattr(tc, "id", ""),
                        "function": {
                            "name": getattr(getattr(tc, "function", None), "name", ""),
                            "arguments": getattr(getattr(tc, "function", None), "arguments", "{}"),
                        },
                    }
                    for tc in message.tool_calls
                ]
            return (text, parse_openai_like_tool_calls(raw_tool_calls))

        # dict fallback
        choices = response.get("choices", []) if isinstance(response, dict) else []
        if not choices:
            return ("", None)

        message = choices[0].get("message", {})
        text = message.get("content", "") or ""
        tool_calls = parse_openai_like_tool_calls(message.get("tool_calls"))
        return (text, tool_calls)
