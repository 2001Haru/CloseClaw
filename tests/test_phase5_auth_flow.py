"""Phase5 P1.5 integration tests for auth + visible response coordination."""

import asyncio
from datetime import datetime, timezone

import pytest

from closeclaw.agents.core import AgentCore
from closeclaw.types import AgentConfig, AuthorizationResponse, Message, Session, Tool, ToolCall, ToolType


class ReadThenWriteLLM:
    """LLM that first asks to read then write, then provides final summary."""

    def __init__(self):
        self.calls = 0

    async def generate(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return (
                "The content is read. Next I will write ABCDEF into poet.txt.",
                [
                    ToolCall(
                        tool_id="tc_read",
                        name="read_file",
                        arguments={"path": "D:/HALcode/poet.txt"},
                    ),
                    ToolCall(
                        tool_id="tc_write",
                        name="write_file",
                        arguments={"path": "D:/HALcode/poet.txt", "content": "ABCDEF"},
                    ),
                ],
            )

        return "Done. I have completed the requested operations.", None


class StepwiseReadThenWriteLLM:
    """LLM that emits read and write in separate planning turns."""

    def __init__(self):
        self.calls = 0

    async def generate(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return (
                "Let me read the file first.",
                [
                    ToolCall(
                        tool_id="tc_read_stepwise",
                        name="read_file",
                        arguments={"path": "D:/HALcode/poet.txt"},
                    )
                ],
            )

        if self.calls == 2:
            return (
                "Read complete. Now I will write ABCDEF into poet.txt.",
                [
                    ToolCall(
                        tool_id="tc_write_stepwise",
                        name="write_file",
                        arguments={"path": "D:/HALcode/poet.txt", "content": "ABCDEF"},
                    )
                ],
            )

        return "Done. I have completed the requested operations.", None


class ContextAwareReadThenWriteLLM:
    """LLM that repeats read unless it can see prior tool result in messages."""

    async def generate(self, messages, tools, **kwargs):
        saw_read_result = any(
            m.get("role") == "tool" and "QWERT" in (m.get("content") or "")
            for m in messages
        )

        if not saw_read_result:
            return (
                "I will read poet.txt first.",
                [
                    ToolCall(
                        tool_id="tc_read_context_aware",
                        name="read_file",
                        arguments={"path": "D:/HALcode/poet.txt"},
                    )
                ],
            )

        return (
            "Read completed. Requesting write now.",
            [
                ToolCall(
                    tool_id="tc_write_context_aware",
                    name="write_file",
                    arguments={"path": "D:/HALcode/poet.txt", "content": "GOOD"},
                )
            ],
        )


class AuthThenSynthesizeContextLLM:
    """LLM used to verify synthesis prompt richness after auth approval."""

    def __init__(self):
        self.calls = 0

    async def generate(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return (
                "I will write the sentence.",
                [
                    ToolCall(
                        tool_id="tc_append_context",
                        name="write_file",
                        arguments={
                            "path": "D:/HALcode/CloseClaw/CloseClaw/closeclaw_intro.txt",
                            "content": "I'm GOOD!",
                        },
                    )
                ],
            )

        has_tool_trace = any(m.get("role") == "assistant" and m.get("tool_calls") for m in messages)
        rich_prompt = len(messages) > 3
        if has_tool_trace and rich_prompt:
            return "CONTEXT_OK", None
        return f"CONTEXT_BAD len={len(messages)} trace={has_tool_trace}", None


class SelectiveAuthMiddleware:
    """Require auth for write_file and allow everything else."""

    async def check_permission(self, tool, arguments, session, user_id):
        if tool.name == "write_file" and not arguments.get("_force_execute"):
            auth_id = f"auth_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
            return {
                "status": "requires_auth",
                "auth_request_id": auth_id,
                "auth_request": {
                    "id": auth_id,
                    "tool_name": tool.name,
                    "description": "write_file requires authorization",
                    "arguments": arguments,
                    "operation_type": "file_write",
                    "diff_preview": None,
                },
                "tool_name": tool.name,
                "arguments": arguments,
                "operation_type": "file_write",
                "description": "write_file requires authorization",
                "diff_preview": None,
            }

        return {"status": "allow"}


@pytest.fixture
def phase5_agent(temp_workspace):
    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["orchestrator"] = {
        "max_steps": 8,
    }

    agent = AgentCore(
        agent_id="agent_phase5_p15",
        llm_provider=ReadThenWriteLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_p15",
        user_id="cli_user",
        channel_type="cli",
    )

    async def read_handler(path: str):
        return "QWERT"

    async def write_handler(path: str, content: str):
        return f"File written: {path}"

    agent.register_tool(Tool(
        name="read_file",
        description="Read file",
        handler=read_handler,
        type=ToolType.FILE,
        need_auth=False,
        parameters={"path": {"type": "string"}},
    ))
    agent.register_tool(Tool(
        name="write_file",
        description="Write file",
        handler=write_handler,
        type=ToolType.FILE,
        need_auth=True,
        parameters={
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
    ))

    agent.set_middleware_chain(SelectiveAuthMiddleware())
    return agent


@pytest.fixture
def phase5_agent_stepwise(temp_workspace):
    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["orchestrator"] = {
        "max_steps": 8,
    }

    agent = AgentCore(
        agent_id="agent_phase5_p15_stepwise",
        llm_provider=StepwiseReadThenWriteLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_p15_stepwise",
        user_id="cli_user",
        channel_type="cli",
    )

    async def read_handler(path: str):
        return "QWERT"

    async def write_handler(path: str, content: str):
        return f"File written: {path}"

    agent.register_tool(Tool(
        name="read_file",
        description="Read file",
        handler=read_handler,
        type=ToolType.FILE,
        need_auth=False,
        parameters={"path": {"type": "string"}},
    ))
    agent.register_tool(Tool(
        name="write_file",
        description="Write file",
        handler=write_handler,
        type=ToolType.FILE,
        need_auth=True,
        parameters={
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
    ))

    agent.set_middleware_chain(SelectiveAuthMiddleware())
    return agent


@pytest.fixture
def phase5_agent_context_aware(temp_workspace):
    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["orchestrator"] = {
        "max_steps": 8,
    }

    agent = AgentCore(
        agent_id="agent_phase5_p15_context_aware",
        llm_provider=ContextAwareReadThenWriteLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_p15_context_aware",
        user_id="cli_user",
        channel_type="cli",
    )

    async def read_handler(path: str):
        return "QWERT"

    async def write_handler(path: str, content: str):
        return f"File written: {path}"

    agent.register_tool(Tool(
        name="read_file",
        description="Read file",
        handler=read_handler,
        type=ToolType.FILE,
        need_auth=False,
        parameters={"path": {"type": "string"}},
    ))
    agent.register_tool(Tool(
        name="write_file",
        description="Write file",
        handler=write_handler,
        type=ToolType.FILE,
        need_auth=True,
        parameters={
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
    ))

    agent.set_middleware_chain(SelectiveAuthMiddleware())
    return agent


@pytest.fixture
def phase5_agent_auth_context_synthesis(temp_workspace):
    config = AgentConfig(model="openai/gpt-4", temperature=0.0)
    config.metadata["orchestrator"] = {
        "max_steps": 8,
    }

    agent = AgentCore(
        agent_id="agent_phase5_auth_context_synth",
        llm_provider=AuthThenSynthesizeContextLLM(),
        config=config,
        workspace_root=temp_workspace,
    )
    agent.current_session = Session(
        session_id="s_phase5_auth_context_synth",
        user_id="cli_user",
        channel_type="cli",
    )

    async def write_handler(path: str, content: str):
        return f"File written: {path}"

    agent.register_tool(Tool(
        name="write_file",
        description="Write file",
        handler=write_handler,
        type=ToolType.FILE,
        need_auth=True,
        parameters={
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
    ))

    agent.set_middleware_chain(SelectiveAuthMiddleware())
    return agent


@pytest.mark.asyncio
async def test_phase5_read_then_write_requires_auth_same_turn(phase5_agent):
    message = Message(
        id="u1",
        channel_type="cli",
        sender_id="cli_user",
        sender_name="User",
        content="First read poet.txt, then write ABCDEF into it.",
        timestamp=datetime.now(timezone.utc),
    )

    result = await phase5_agent.process_message(message)

    assert result["requires_auth"] is True
    assert len(result["tool_calls"]) == 2, "Expected read + write in one turn"
    assert result["tool_calls"][0]["name"] == "read_file"
    assert result["tool_calls"][1]["name"] == "write_file"
    assert result["tool_results"][0]["status"] == "success"
    assert result["tool_results"][1]["status"] == "auth_required"


@pytest.mark.asyncio
async def test_phase5_stepwise_read_then_write_requires_auth_same_turn(phase5_agent_stepwise):
    message = Message(
        id="u1_stepwise",
        channel_type="cli",
        sender_id="cli_user",
        sender_name="User",
        content="First read poet.txt, then write ABCDEF into it.",
        timestamp=datetime.now(timezone.utc),
    )

    result = await phase5_agent_stepwise.process_message(message)

    assert result["requires_auth"] is True
    assert len(result["tool_calls"]) == 2, "Expected read + write in one turn across separate planning steps"
    assert result["tool_calls"][0]["name"] == "read_file"
    assert result["tool_calls"][1]["name"] == "write_file"
    assert result["tool_results"][0]["status"] == "success"
    assert result["tool_results"][1]["status"] == "auth_required"


@pytest.mark.asyncio
async def test_phase5_no_repeated_read_when_tool_result_visible(phase5_agent_context_aware):
    message = Message(
        id="u_ctx_aware",
        channel_type="cli",
        sender_id="cli_user",
        sender_name="User",
        content="First read poet.txt, then write GOOD into it",
        timestamp=datetime.now(timezone.utc),
    )

    result = await phase5_agent_context_aware.process_message(message)

    assert result["requires_auth"] is True
    assert len(result["tool_calls"]) == 2, "Expected planner to move from read to write instead of repeating read"
    assert result["tool_calls"][0]["name"] == "read_file"
    assert result["tool_calls"][1]["name"] == "write_file"
    assert result["tool_results"][0]["status"] == "success"
    assert result["tool_results"][1]["status"] == "auth_required"


@pytest.mark.asyncio
async def test_phase5_synthesis_prompt_uses_full_context_after_auth(phase5_agent_auth_context_synthesis):
    message = Message(
        id="u_ctx_synth",
        channel_type="cli",
        sender_id="cli_user",
        sender_name="User",
        content="Add another sentence \"I'm GOOD!\" to closeclaw_intro.txt.",
        timestamp=datetime.now(timezone.utc),
    )

    first = await phase5_agent_auth_context_synthesis.process_message(message)
    assert first["requires_auth"] is True
    auth_request_id = first["auth_request_id"]

    auth_result = await phase5_agent_auth_context_synthesis.approve_auth_request(
        auth_request_id=auth_request_id,
        user_id="cli_user",
        approved=True,
    )
    resumed = await phase5_agent_auth_context_synthesis._phase5_resume_after_auth(auth_request_id, auth_result)

    assert resumed is not None
    assert resumed["response"] == "CONTEXT_OK"


@pytest.mark.asyncio
async def test_phase5_auth_request_keeps_visible_assistant_message(phase5_agent):
    message = Message(
        id="u2",
        channel_type="cli",
        sender_id="cli_user",
        sender_name="User",
        content="Read then write poet.txt",
        timestamp=datetime.now(timezone.utc),
    )

    result = await phase5_agent.process_message(message)

    assert result["requires_auth"] is True
    assert result.get("assistant_message"), "Expected visible assistant text before auth request"
    assert "Next I will write ABCDEF" in result["assistant_message"]


@pytest.mark.asyncio
async def test_phase5_auth_approve_auto_finalize(phase5_agent):
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(Message(
        id="u3",
        channel_type="cli",
        sender_id="cli_user",
        sender_name="User",
        content="Read then write poet.txt",
        timestamp=datetime.now(timezone.utc),
    ))

    outputs = []

    async def message_input_fn():
        return await queue.get()

    async def message_output_fn(payload):
        outputs.append(payload)

    async def auth_response_fn(auth_request_id: str, timeout: float):
        await asyncio.sleep(0.05)
        return AuthorizationResponse(
            auth_request_id=auth_request_id,
            user_id="cli_user",
            approved=True,
        )

    run_task = asyncio.create_task(phase5_agent.run(
        session_id="s_phase5_p15_run",
        user_id="cli_user",
        channel_type="cli",
        message_input_fn=message_input_fn,
        message_output_fn=message_output_fn,
        auth_response_fn=auth_response_fn,
    ))

    await asyncio.sleep(0.3)
    await queue.put(None)
    await asyncio.wait_for(run_task, timeout=2)

    assistant_msgs = [o for o in outputs if o.get("type") in {"assistant_message", "response"}]
    auth_msgs = [o for o in outputs if o.get("type") == "auth_request"]

    assert auth_msgs, "Expected auth request output"
    assert assistant_msgs, "Expected assistant message outputs"
    assert any("completed" in (o.get("response") or "").lower() for o in assistant_msgs), (
        "Expected auto-finalized response after approval"
    )





