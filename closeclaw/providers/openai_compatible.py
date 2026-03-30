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
_OPENAI_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"})


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
        moonshot_mode = self._is_moonshot_like()
        if moonshot_mode:
            cleaned_messages = self._with_reasoning_content_for_tool_calls(cleaned_messages)

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

        async def _request(request_body: dict[str, Any]) -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=request_body)
                response.raise_for_status()
                return response.json()

        try:
            data = await run_with_transient_retry(lambda: _request(body))
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
            # Compatibility fallback: some models (e.g. Kimi variants) only accept temperature=1.
            if self._should_retry_with_temperature_one(e, body):
                retry_body = dict(body)
                retry_body["temperature"] = 1
                try:
                    data = await run_with_transient_retry(lambda: _request(retry_body))
                    text, tool_calls = self._parse_response(data)
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    logger.info(
                        "provider=openai-compatible model=%s fallback=temperature_1 latency_ms=%.2f tool_calls=%s",
                        self.model,
                        latency_ms,
                        len(tool_calls or []),
                    )
                    return text, tool_calls
                except httpx.HTTPStatusError as retry_exc:
                    error_body = retry_exc.response.text[:500] if retry_exc.response else "No response"
                    raise RuntimeError(f"LLM API error {retry_exc.response.status_code}: {error_body}")

            # Compatibility fallback: Moonshot thinking mode can require reasoning_content
            # for assistant messages that include tool_calls.
            if moonshot_mode and self._should_retry_with_reasoning_content(e):
                retry_body = dict(body)
                retry_body["messages"] = self._with_reasoning_content_for_tool_calls(
                    list(retry_body.get("messages", []))
                )
                try:
                    data = await run_with_transient_retry(lambda: _request(retry_body))
                    text, tool_calls = self._parse_response(data)
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    logger.info(
                        "provider=openai-compatible model=%s fallback=reasoning_content_backfill latency_ms=%.2f tool_calls=%s",
                        self.model,
                        latency_ms,
                        len(tool_calls or []),
                    )
                    return text, tool_calls
                except httpx.HTTPStatusError as retry_exc:
                    error_body = retry_exc.response.text[:500] if retry_exc.response else "No response"
                    raise RuntimeError(f"LLM API error {retry_exc.response.status_code}: {error_body}")

            error_body = e.response.text[:500] if e.response else "No response"
            raise RuntimeError(f"LLM API error {e.response.status_code}: {error_body}")

    @staticmethod
    def _should_retry_with_temperature_one(exc: httpx.HTTPStatusError, request_body: dict[str, Any]) -> bool:
        """Return True when API explicitly reports model only allows temperature=1."""
        if not exc.response or exc.response.status_code != 400:
            return False
        try:
            current_temp = float(request_body.get("temperature", 0))
        except Exception:
            current_temp = 0.0
        if abs(current_temp - 1.0) < 1e-9:
            return False

        raw = (exc.response.text or "").lower()
        if "invalid temperature" in raw and "only 1 is allowed" in raw:
            return True
        # Broader fallback for providers with similar wording.
        if "temperature" in raw and "only 1" in raw and "allowed" in raw:
            return True
        return False

    def _is_moonshot_like(self) -> bool:
        base = (self.base_url or "").lower()
        model = (self.model or "").lower()
        return ("moonshot" in base) or ("moonshot" in model) or ("kimi" in model)

    @staticmethod
    def _with_reasoning_content_for_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Backfill reasoning_content for assistant tool_call messages when missing."""
        fixed: list[dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "")).strip().lower()
            has_tool_calls = bool(msg.get("tool_calls"))
            if role == "assistant" and has_tool_calls and "reasoning_content" not in msg:
                patched = dict(msg)
                content = patched.get("content")
                if isinstance(content, str) and content.strip():
                    patched["reasoning_content"] = content
                else:
                    patched["reasoning_content"] = "[reasoning omitted]"
                fixed.append(patched)
            else:
                fixed.append(msg)
        return fixed

    @staticmethod
    def _should_retry_with_reasoning_content(exc: httpx.HTTPStatusError) -> bool:
        if not exc.response or exc.response.status_code != 400:
            return False
        raw = (exc.response.text or "").lower()
        return (
            "thinking is enabled" in raw
            and "reasoning_content" in raw
            and "tool call message" in raw
        )

    def _parse_response(self, data: dict[str, Any]) -> tuple[str, Optional[list[ToolCall]]]:
        choices = data.get("choices", [])
        if not choices:
            logger.warning("LLM returned empty choices")
            return ("", None)

        message = choices[0].get("message", {})
        text = message.get("content", "") or ""
        tool_calls = parse_openai_like_tool_calls(message.get("tool_calls"))

        # Fallback: Many 3rd-party proxies (e.g., OhMyGPT mapping Gemini API) fail to map 
        # native tool responses back into the OpenAI `tool_calls` array, instead dumping 
        # the raw JSON tool request into the text `content`. 
        if not tool_calls and text:
            import re
            import json
            import uuid
            
            extracted_calls = []
            exact_matches_to_strip = []

            # 1. Look for proxy-synthesized text: `Calling <name> function with parameters: <json>`
            proxy_patterns = re.finditer(r"Calling\s+([a-zA-Z0-9_]+)\s+function\s+with\s+parameters:\s*(\{.*?\})", text, re.DOTALL)
            for match in proxy_patterns:
                func_name = match.group(1)
                args_str = match.group(2)
                try:
                    args = json.loads(args_str)
                    extracted_calls.append({"name": func_name, "arguments": args})
                    exact_matches_to_strip.append(match.group(0))
                except json.JSONDecodeError:
                    pass
            
            # 2. Look for ```json ... ``` blocks
            if not extracted_calls:
                json_blocks = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
                blocks_to_check = json_blocks if json_blocks else [text.strip()]
                
                for block in blocks_to_check:
                    if not block:
                        continue
                    try:
                        parsed = json.loads(block)
                        
                        # Handle single object {"name": "tool", "arguments": {...}}
                        if isinstance(parsed, dict) and "name" in parsed and "arguments" in parsed:
                            extracted_calls.append(parsed)
                        # Handle array of objects [{"name": "tool", ...}]
                        elif isinstance(parsed, list) and all(isinstance(x, dict) and "name" in x for x in parsed):
                            extracted_calls.extend(parsed)
                        # Handle wrapped format {"tool_calls": [...]}
                        elif isinstance(parsed, dict) and "tool_calls" in parsed and isinstance(parsed["tool_calls"], list):
                            extracted_calls.extend(parsed["tool_calls"])
                    except json.JSONDecodeError:
                        continue
                        
            if extracted_calls:
                tool_calls = []
                for call in extracted_calls:
                    name = call.get("name")
                    args = call.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"raw": args}
                            
                    tool_calls.append(ToolCall(
                        tool_id=call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        name=str(name),
                        arguments=args,
                    ))
                # Clear the JSON/proxy block from text so it doesn't leak to the user
                text = re.sub(r"Calling\s+[a-zA-Z0-9_]+\s+function\s+with\s+parameters:\s*\{.*?\}", "[Tool calling requested...]", text, flags=re.DOTALL)
                text = re.sub(r"```(?:json)?\s*.*?\s*```", "[Tool calling requested...]", text, flags=re.DOTALL)
                
                # If the entire message was just a fallback string without other useful text, make it exactly "[Tool calling requested...]"
                if not text.strip().replace("[Tool calling requested...]", "").strip():
                    text = "[Tool calling requested...]"

        return (text, tool_calls)
