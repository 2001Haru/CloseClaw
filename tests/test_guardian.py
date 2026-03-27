"""Tests for consensus guardian behavior."""

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

