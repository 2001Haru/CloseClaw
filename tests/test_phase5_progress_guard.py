"""P3-A tests for progress policy and no-progress stop behavior."""

from datetime import datetime

import pytest

from closeclaw.agents.core import AgentCore
from closeclaw.orchestrator.progress import assess_progress
from closeclaw.types import AgentConfig, Message, Session, Tool, ToolCall, ToolType, Zone


def test_assess_progress_replan_threshold():
    snap1 = assess_progress(previous_stagnation=0, tool_status="error", no_progress_limit=2)
    assert snap1.progress_made is False
    assert snap1.stagnation_count == 1
    assert snap1.replan_required is False

    snap2 = assess_progress(previous_stagnation=snap1.stagnation_count, tool_status="blocked", no_progress_limit=2)
    assert snap2.progress_made is False
    assert snap2.stagnation_count == 2
    assert snap2.replan_required is True


@pytest.mark.asyncio
async def test_agent_stops_on_no_progress_limit(temp_workspace):
    class FailingLoopLLM:
        async def generate(self, messages, tools, **kwargs):
            return (
                "trying tool",
                [ToolCall(tool_id=f"tc_{len(messages)}", name="always_fail", arguments={})],
            )

    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["phase5"] = {
        "max_steps": 6,
        "no_progress_limit": 2,
    }

    agent = AgentCore(
        agent_id="agent_phase5_progress",
        llm_provider=FailingLoopLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_progress",
        user_id="u1",
        channel_type="cli",
    )

    async def failing_handler():
        raise RuntimeError("boom")

    agent.register_tool(Tool(
        name="always_fail",
        description="Always fails",
        handler=failing_handler,
        type=ToolType.FILE,
        zone=Zone.ZONE_A,
        parameters={},
    ))

    result = await agent.process_message(Message(
        id="m1",
        channel_type="cli",
        sender_id="u1",
        sender_name="User",
        content="run failing chain",
        timestamp=datetime.utcnow(),
    ))

    assert result["decision"] == "no_progress_limit_reached"
    assert result["requires_auth"] is False
    assert len(result["tool_results"]) >= 2
    assert "plan_update" in result
    payload = result["plan_update"]
    assert payload.get("goal")
    assert payload.get("current_step")
    assert isinstance(payload.get("remaining_steps"), list)
    assert isinstance(payload.get("done_criteria"), list)
    assert isinstance(payload.get("risk"), list)
    assert isinstance(payload.get("todo_snapshot"), list)
