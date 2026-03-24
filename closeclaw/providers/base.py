"""Base provider utilities and shared contracts for CloseClaw providers."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional, Protocol

import httpx

from ..types import ToolCall


class ProviderProtocol(Protocol):
    """Protocol used by AgentCore and services for LLM provider objects."""

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[str, Optional[list[ToolCall]]]:
        ...


TRANSIENT_ERROR_MARKERS = (
    "429",
    "rate limit",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
)


def parse_openai_like_tool_calls(raw_tool_calls: Any) -> Optional[list[ToolCall]]:
    """Convert OpenAI-compatible tool calls payload into CloseClaw ToolCall list."""
    if not raw_tool_calls:
        return None

    tool_calls: list[ToolCall] = []
    for tc in raw_tool_calls:
        if not isinstance(tc, dict):
            continue

        function = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
        args_raw = function.get("arguments", {})

        if isinstance(args_raw, str):
            try:
                arguments = json.loads(args_raw)
            except json.JSONDecodeError:
                arguments = {"raw": args_raw}
        elif isinstance(args_raw, dict):
            arguments = args_raw
        else:
            arguments = {}

        tool_calls.append(
            ToolCall(
                tool_id=str(tc.get("id", "")),
                name=str(function.get("name", "")),
                arguments=arguments,
            )
        )

    return tool_calls or None


def sanitize_empty_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize empty/invalid content blocks to reduce provider-side 400 errors."""
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        role = msg.get("role")

        if isinstance(content, str) and not content:
            item = dict(msg)
            if role == "assistant" and msg.get("tool_calls"):
                item["content"] = None
            else:
                item["content"] = "(empty)"
            cleaned.append(item)
            continue

        cleaned.append(msg)

    return cleaned


def sanitize_request_messages(
    messages: list[dict[str, Any]],
    allowed_keys: frozenset[str],
) -> list[dict[str, Any]]:
    """Keep only provider-safe message keys and normalize assistant content."""
    sanitized: list[dict[str, Any]] = []
    for msg in messages:
        clean = {k: v for k, v in msg.items() if k in allowed_keys}
        if clean.get("role") == "assistant" and "content" not in clean:
            clean["content"] = None
        sanitized.append(clean)
    return sanitized


def is_transient_error(exc: Exception) -> bool:
    """Return True if exception looks like a retryable transient provider failure."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response else 0
        if status in {429, 500, 502, 503, 504}:
            return True

    message = str(exc).lower()
    return any(marker in message for marker in TRANSIENT_ERROR_MARKERS)


async def run_with_transient_retry(
    operation: Callable[[], Awaitable[Any]],
    *,
    retry_delays: tuple[float, ...] = (0.5, 1.0, 2.0),
    should_retry: Callable[[Exception], bool] = is_transient_error,
) -> Any:
    """Execute async operation and retry only for transient failures."""
    for attempt, delay in enumerate(retry_delays, start=1):
        try:
            return await operation()
        except Exception as exc:
            if not should_retry(exc):
                raise
            if attempt == len(retry_delays):
                raise
            await asyncio.sleep(delay)

    return await operation()
