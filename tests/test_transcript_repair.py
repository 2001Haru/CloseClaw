"""Tests for transcript repair behavior."""

import tempfile

import pytest

from closeclaw.agents.core import AgentCore
from closeclaw.types import AgentConfig


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_llm_provider():
    class MockProvider:
        async def generate(self, messages, tools, **kwargs):
            return "mock response", None

    return MockProvider()


@pytest.fixture
def agent(temp_workspace, mock_llm_provider):
    config = AgentConfig(model="gpt-4", system_prompt="You are a test agent.")
    return AgentCore(
        agent_id="test-agent",
        llm_provider=mock_llm_provider,
        config=config,
        workspace_root=temp_workspace,
        admin_user_id="test-user",
    )


class TestTranscriptRepair:
    def test_orphan_tool_call_repaired(self, agent):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "I'll help",
                "tool_calls": [
                    {
                        "id": "call_001",
                        "type": "function",
                        "function": {"name": "do_work", "arguments": "{}"},
                    }
                ],
            },
            {"role": "user", "content": "What's the result?"},
        ]

        repaired = agent._repair_transcript(messages)

        tool_messages = [msg for msg in repaired if msg.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0].get("tool_call_id") == "call_001"

    def test_orphan_tool_result_removed(self, agent):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "tool", "tool_call_id": "orphan_call_id", "content": "some result"},
            {"role": "user", "content": "Next message"},
        ]

        repaired = agent._repair_transcript(messages)
        assert not any(msg.get("tool_call_id") == "orphan_call_id" for msg in repaired)
