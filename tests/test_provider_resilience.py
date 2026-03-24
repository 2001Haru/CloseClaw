import asyncio

import httpx
import pytest

from closeclaw.providers.base import run_with_transient_retry, sanitize_request_messages, is_transient_error


@pytest.mark.asyncio
async def test_run_with_transient_retry_retries_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    async def _noop_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    async def _operation():
        attempts["n"] += 1
        if attempts["n"] == 1:
            req = httpx.Request("POST", "https://example.com")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("rate limited", request=req, response=resp)
        return "ok"

    result = await run_with_transient_retry(_operation, retry_delays=(0.01, 0.01))

    assert result == "ok"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_run_with_transient_retry_does_not_retry_non_transient(monkeypatch):
    attempts = {"n": 0}

    async def _noop_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    async def _operation():
        attempts["n"] += 1
        raise ValueError("bad request payload")

    with pytest.raises(ValueError):
        await run_with_transient_retry(_operation, retry_delays=(0.01, 0.01))

    assert attempts["n"] == 1


def test_is_transient_error_detects_http_503():
    req = httpx.Request("POST", "https://example.com")
    resp = httpx.Response(503, request=req)
    exc = httpx.HTTPStatusError("service unavailable", request=req, response=resp)
    assert is_transient_error(exc) is True


def test_sanitize_request_messages_keeps_allowed_keys_only():
    messages = [
        {
            "role": "assistant",
            "content": "hi",
            "tool_calls": [],
            "unexpected": "drop-me",
        }
    ]

    clean = sanitize_request_messages(messages, frozenset({"role", "content", "tool_calls"}))
    assert "unexpected" not in clean[0]
