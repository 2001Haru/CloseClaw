"""Phase 2 acceptance tests: end-to-end verification for core components."""

import pytest
import asyncio
from datetime import datetime

from closeclaw.agents import TaskManager
from closeclaw.agents.core import AgentCore
from closeclaw.types import TaskStatus, AgentConfig, Message


class DummyLLM:
    async def generate(self, messages, tools, **kwargs):
        # No tool calls by default; simple echo
        return ("ok", [])


@pytest.mark.asyncio
async def test_agent_task_integration_and_state_persistence(tmp_path):
    """Verify AgentCore integration with TaskManager and state persistence."""
    tm = TaskManager()

    # Simple tool
    async def quick(x: int = 1):
        await asyncio.sleep(0.01)
        return {"x": x}

    tm.register_tool_handler("quick", quick)

    # Create agent with dummy LLM
    config = AgentConfig(model="test-model", system_prompt="test", temperature=0.0)
    agent = AgentCore(agent_id="agent1", llm_provider=DummyLLM(), config=config, workspace_root=str(tmp_path))
    agent.set_task_manager(tm)

    # Start session
    await agent.start_session(session_id="s1", user_id="u1", channel_type="cli")

    # Create background tasks through agent
    t1 = await agent.create_background_task("quick", {"x": 5})
    t2 = await agent.create_background_task("quick", {"x": 6})

    # Allow completion
    await asyncio.sleep(0.1)

    completed = await agent.poll_background_tasks()
    # completed is a dict from TaskManager.poll_results()
    # Ensure TaskManager has moved tasks to completed_results
    assert t1 in tm.completed_results
    assert tm.completed_results[t1].status == TaskStatus.COMPLETED
    assert tm.completed_results[t1].result["x"] == 5

    # Save agent state snapshot
    state = await agent._save_state()
    assert "completed_results" in state

    # Restore into a fresh TaskManager via agent._restore_state
    new_tm = TaskManager()
    new_agent = AgentCore(agent_id="agent1", llm_provider=DummyLLM(), config=config, workspace_root=str(tmp_path))
    new_agent.set_task_manager(new_tm)
    await new_agent._restore_state(state)

    # Ensure restored tasks exist in new manager
    assert t1 in new_tm.completed_results
    assert new_tm.completed_results[t1].status == TaskStatus.COMPLETED
