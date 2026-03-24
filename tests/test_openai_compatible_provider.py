import asyncio

import httpx
import pytest

from closeclaw.providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_openai_provider_retries_transient_429(monkeypatch):
    class FakeAsyncClient:
        call_count = 0

        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            FakeAsyncClient.call_count += 1
            req = httpx.Request("POST", url)
            if FakeAsyncClient.call_count == 1:
                return httpx.Response(429, request=req, json={"error": "rate limit"})
            return httpx.Response(
                200,
                request=req,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "ok", "tool_calls": []},
                        }
                    ]
                },
            )

    async def _noop_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("closeclaw.providers.openai_compatible.httpx.AsyncClient", FakeAsyncClient)

    provider = OpenAICompatibleProvider(api_key="k", model="gpt-4o-mini")
    text, tool_calls = await provider.generate(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
    )

    assert text == "ok"
    assert tool_calls is None
    assert FakeAsyncClient.call_count == 2
