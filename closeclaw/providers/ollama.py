"""Ollama provider implementation for local development."""

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
_OLLAMA_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})


class OllamaProvider:
    """LLM provider for local Ollama native API.

    Uses Ollama's `/api/chat` endpoint by default.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        max_tokens: int = 2000,
        timeout_seconds: int = 60,
        thinking_enabled: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort

        logger.info("Ollama provider initialized: model=%s base_url=%s", model, self.base_url)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[str, Optional[list[ToolCall]]]:
        cleaned_messages = sanitize_request_messages(
            sanitize_empty_messages(messages),
            _OLLAMA_MSG_KEYS,
        )

        options: dict[str, Any] = {
            "temperature": kwargs.get("temperature", self.temperature),
            "num_predict": max(1, int(kwargs.get("max_tokens", self.max_tokens))),
        }

        body: dict[str, Any] = {
            "model": self.model,
            "messages": cleaned_messages,
            "stream": False,
            "options": options,
        }

        if tools:
            body["tools"] = tools

        url = f"{self.base_url}/api/chat"
        headers = {
            "Content-Type": "application/json",
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
            logger.info(
                "provider=ollama model=%s latency_ms=%.2f tool_calls=%s",
                self.model,
                latency_ms,
                len(tool_calls or []),
            )
            return text, tool_calls
        except httpx.TimeoutException:
            raise TimeoutError(f"Ollama request timed out after {self.timeout_seconds}s")
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            raise RuntimeError(f"Ollama API error {e.response.status_code}: {error_body}")

    def _parse_response(self, data: dict[str, Any]) -> tuple[str, Optional[list[ToolCall]]]:
        message = data.get("message", {}) if isinstance(data, dict) else {}
        if not isinstance(message, dict):
            logger.warning("Ollama returned malformed message payload")
            return ("", None)

        text = message.get("content", "") or ""

        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            normalized_tool_calls: list[dict[str, Any]] = []
            for idx, tc in enumerate(raw_tool_calls, start=1):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                normalized_tool_calls.append(
                    {
                        "id": str(tc.get("id") or f"ollama-tool-{idx}"),
                        "function": {
                            "name": str(fn.get("name", "")),
                            "arguments": fn.get("arguments", {}),
                        },
                    }
                )
            tool_calls = parse_openai_like_tool_calls(normalized_tool_calls)
        else:
            tool_calls = None

        return text, tool_calls
