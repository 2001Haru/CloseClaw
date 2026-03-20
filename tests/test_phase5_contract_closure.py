"""Phase5 closure tests: public interfaces and output contract consistency."""

from datetime import datetime

import pytest

import closeclaw.orchestrator as orchestrator
from closeclaw.agents.core import AgentCore
from closeclaw.types import AgentConfig, Message, Session, Tool, ToolCall, ToolType, Zone


REQUIRED_OUTPUT_KEYS = {
    "response",
    "tool_calls",
    "tool_results",
    "requires_auth",
    "memory_flushed",
}


def test_orchestrator_public_exports_exist():
    """Phase5 public API should export core orchestrator and P4 interface symbols."""
    required_symbols = [
        "Action",
        "Decision",
        "Observation",
        "RunBudget",
        "RunState",
        "OrchestratorEngine",
        "PreActContextGuard",
        "BeforePlanHook",
        "PlanPolicy",
        "ProgressPolicy",
        "TodoStore",
        "SubtaskStatus",
        "SubtaskSpec",
        "SubtaskHandle",
        "SubtaskRegistry",
        "SubtaskRegistryError",
    ]

    missing = [name for name in required_symbols if not hasattr(orchestrator, name)]
    assert not missing, f"Missing orchestrator exports: {missing}"


@pytest.mark.asyncio
async def test_phase5_output_contract_success_path(temp_workspace):
    class DirectLLM:
        async def generate(self, messages, tools, **kwargs):
            return "direct answer", None

    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["phase5"] = {"max_steps": 4}

    agent = AgentCore(
        agent_id="agent_phase5_contract_success",
        llm_provider=DirectLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_contract_success",
        user_id="u1",
        channel_type="cli",
    )

    output = await agent.process_message(Message(
        id="m1",
        channel_type="cli",
        sender_id="u1",
        sender_name="User",
        content="say hi",
        timestamp=datetime.utcnow(),
    ))

    assert REQUIRED_OUTPUT_KEYS.issubset(output.keys())
    assert output["requires_auth"] is False


@pytest.mark.asyncio
async def test_phase5_output_contract_no_progress_path(temp_workspace):
    class AlwaysFailLLM:
        async def generate(self, messages, tools, **kwargs):
            return "trying", [ToolCall(tool_id=f"tc_{len(messages)}", name="always_fail", arguments={})]

    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["phase5"] = {
        "max_steps": 6,
        "no_progress_limit": 2,
    }

    agent = AgentCore(
        agent_id="agent_phase5_contract_no_progress",
        llm_provider=AlwaysFailLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_contract_no_progress",
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

    output = await agent.process_message(Message(
        id="m1",
        channel_type="cli",
        sender_id="u1",
        sender_name="User",
        content="run failing chain",
        timestamp=datetime.utcnow(),
    ))

    assert REQUIRED_OUTPUT_KEYS.issubset(output.keys())
    assert output.get("decision") == "no_progress_limit_reached"
    assert isinstance(output.get("plan_update"), dict)


@pytest.mark.asyncio
async def test_phase5_output_contract_auth_required_path(temp_workspace):
    class AuthLLM:
        async def generate(self, messages, tools, **kwargs):
            return "need auth", [ToolCall(tool_id="tc_auth", name="write_file", arguments={"path": "a", "content": "b"})]

    class AuthMiddleware:
        async def check_permission(self, tool, arguments, session, user_id):
            return {
                "status": "requires_auth",
                "auth_request_id": "auth_1",
                "tool_name": tool.name,
                "arguments": arguments,
                "operation_type": "file_write",
                "description": "write_file requires authorization",
                "diff_preview": None,
            }

    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["phase5"] = {"max_steps": 4}

    agent = AgentCore(
        agent_id="agent_phase5_contract_auth",
        llm_provider=AuthLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_contract_auth",
        user_id="u1",
        channel_type="cli",
    )

    async def write_handler(path: str, content: str):
        return "ok"

    agent.register_tool(Tool(
        name="write_file",
        description="Write file",
        handler=write_handler,
        type=ToolType.FILE,
        zone=Zone.ZONE_C,
        parameters={"path": {"type": "string"}, "content": {"type": "string"}},
    ))
    agent.set_middleware_chain(AuthMiddleware())

    output = await agent.process_message(Message(
        id="m_auth",
        channel_type="cli",
        sender_id="u1",
        sender_name="User",
        content="please write",
        timestamp=datetime.utcnow(),
    ))

    assert REQUIRED_OUTPUT_KEYS.issubset(output.keys())
    assert output["requires_auth"] is True
    assert output.get("auth_request_id") == "auth_1"
