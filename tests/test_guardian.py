"""Tests for consensus guardian behavior."""

import asyncio
import pytest

from closeclaw.safety.guardian import ConsensusGuardian


class _CaptureProvider:
    def __init__(self, response_text: str = '{"decision":"approve","reason_code":"OK","comment":"ok"}'):
        self.response_text = response_text
        self.calls = []

    async def generate(self, messages, tools, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "kwargs": kwargs,
            }
        )
        return self.response_text, None


@pytest.mark.asyncio
async def test_guardian_review_uses_small_deterministic_generation_budget():
    provider = _CaptureProvider()
    guardian = ConsensusGuardian(llm_provider=provider, timeout_seconds=5.0)

    decision = await guardian.review({"tool_name": "write_file", "arguments": {"path": "/tmp/a.txt"}})

    assert decision.approved is True
    assert provider.calls
    call = provider.calls[0]
    assert call["tools"] == []
    assert call["kwargs"].get("temperature") == 0.0
    assert call["kwargs"].get("max_tokens") == 256


@pytest.mark.asyncio
async def test_guardian_timeout_returns_explicit_reason_code():
    class _SlowProvider:
        async def generate(self, messages, tools, **kwargs):
            _ = (messages, tools, kwargs)
            await asyncio.sleep(0.2)
            return '{"decision":"approve","reason_code":"OK","comment":"ok"}', None

    guardian = ConsensusGuardian(llm_provider=_SlowProvider(), timeout_seconds=0.01)
    decision = await guardian.review({"tool_name": "shell", "arguments": {"command": "echo hi"}})

    assert decision.approved is False
    assert decision.reason_code == "GUARDIAN_TIMEOUT"
    assert "timed out" in decision.comment.lower()


@pytest.mark.asyncio
async def test_guardian_error_includes_exception_type():
    class _BrokenProvider:
        async def generate(self, messages, tools, **kwargs):
            _ = (messages, tools, kwargs)
            raise ValueError("boom")

    guardian = ConsensusGuardian(llm_provider=_BrokenProvider(), timeout_seconds=1.0)
    decision = await guardian.review({"tool_name": "shell", "arguments": {"command": "echo hi"}})

    assert decision.approved is False
    assert decision.reason_code == "GUARDIAN_ERROR"
    assert "ValueError" in decision.comment
