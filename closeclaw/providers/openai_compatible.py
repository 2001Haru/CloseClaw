"""OpenAI-compatible provider implementation for CloseClaw."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from ..types import ToolCall
from .base import (
    parse_openai_like_tool_calls,
    run_with_transient_retry,
    sanitize_empty_messages,
    sanitize_request_messages,
)

logger = logging.getLogger(__name__)
_OPENAI_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})


class OpenAICompatibleProvider:
    """LLM provider for OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.0,
        max_tokens: int = 2000,
        timeout_seconds: int = 60,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

        logger.info("OpenAI-compatible provider initialized: model=%s base_url=%s", model, self.base_url)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[str, Optional[list[ToolCall]]]:
        cleaned_messages = sanitize_request_messages(
            sanitize_empty_messages(messages),
            _OPENAI_MSG_KEYS,
        )

        body: dict[str, Any] = {
            "model": self.model,
            "messages": cleaned_messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": max(1, int(kwargs.get("max_tokens", self.max_tokens))),
        }

        if tools:
            body["tools"] = tools
            body["tool_choice"] = kwargs.get("tool_choice", "auto")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        started = time.perf_counter()

        async def _request() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                return response.json()

        try:
            data = await run_with_transient_retry(_request)
            text, tool_calls = self._parse_response(data)

            latency_ms = (time.perf_counter() - started) * 1000.0
            finish_reason = ""
            choices = data.get("choices", [])
            if choices and isinstance(choices[0], dict):
                finish_reason = str(choices[0].get("finish_reason", ""))
            logger.info(
                "provider=openai-compatible model=%s latency_ms=%.2f finish_reason=%s tool_calls=%s",
                self.model,
                latency_ms,
                finish_reason or "unknown",
                len(tool_calls or []),
            )
            return text, tool_calls
        except httpx.TimeoutException:
            raise TimeoutError(f"LLM request timed out after {self.timeout_seconds}s")
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            raise RuntimeError(f"LLM API error {e.response.status_code}: {error_body}")

    def _parse_response(self, data: dict[str, Any]) -> tuple[str, Optional[list[ToolCall]]]:
        choices = data.get("choices", [])
        if not choices:
            logger.warning("LLM returned empty choices")
            return ("", None)

        message = choices[0].get("message", {})
        text = message.get("content", "") or ""
        tool_calls = parse_openai_like_tool_calls(message.get("tool_calls"))

        return (text, tool_calls)
