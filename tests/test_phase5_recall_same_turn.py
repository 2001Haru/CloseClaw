"""Phase5 P1 acceptance tests for same-turn recall completion."""

import pytest
from datetime import datetime, timezone

from closeclaw.agents.core import AgentCore
from closeclaw.types import AgentConfig, Message, Session, ToolCall


class RecallThenSummarizeLLM:
    """First call requests retrieve_memory; second call summarizes results."""

    def __init__(self):
        self.calls = 0

    async def generate(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return (
                "I will check memory first.",
                [
                    ToolCall(
                        tool_id="tc_recall_1",
                        name="retrieve_memory",
                        arguments={"query": "F1 decision"},
                    )
                ],
            )

        return "Summary: previous decision was to use allowlist rollout first.", None


@pytest.mark.asyncio
async def test_phase5_case001_same_turn_completion(temp_workspace):
    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["orchestrator"] = {
        "max_steps": 6,
    }

    agent = AgentCore(
        agent_id="agent_phase5",
        llm_provider=RecallThenSummarizeLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5",
        user_id="u1",
        channel_type="cli",
    )

    user_message = Message(
        id="m1",
        channel_type="cli",
        sender_id="u1",
        sender_name="User",
        content="Do you remember our previous decision about F1?",
        timestamp=datetime.now(timezone.utc),
    )

    result = await agent.process_message(user_message)

    assert result["requires_auth"] is False
    assert result["tool_calls"], "Expected retrieve_memory tool call"
    assert result["tool_results"], "Expected tool execution result"
    assert "Summary:" in result["response"]
    assert result["response"] not in {"OK", "Executed tools.", "Awaiting authorization..."}





