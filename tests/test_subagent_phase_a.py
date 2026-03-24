"""Phase A closed-loop tests for subagent lifecycle."""

import asyncio

import pytest

from closeclaw.agents.task_manager import TaskManager
from closeclaw.subagent.manager import SubagentManager
from closeclaw.types import ToolCall, ToolResult


class _ToolCallStub:
    def to_dict(self):
        return {"name": "noop"}


class _SuccessProvider:
    async def generate(self, messages, tools, **kwargs):
        _ = (messages, tools, kwargs)
        return "done", [_ToolCallStub()]


class _FailureProvider:
    async def generate(self, messages, tools, **kwargs):
        _ = (messages, tools, kwargs)
        raise ValueError("boom")


class _SlowProvider:
    def __init__(self, sleep_s: float = 0.2):
        self._sleep_s = sleep_s

    async def generate(self, messages, tools, **kwargs):
        _ = (messages, tools, kwargs)
        await asyncio.sleep(self._sleep_s)
        return "late", None


class _ToolUsingProvider:
    def __init__(self):
        self.calls = 0
        self.last_tools = []

    async def generate(self, messages, tools, **kwargs):
        _ = (messages, kwargs)
        self.calls += 1
        self.last_tools = tools or []
        if self.calls == 1:
            return "checking", [
                ToolCall(
                    tool_id="tc_1",
                    name="read_file",
                    arguments={"path": "D:/HALcode/CloseClaw_Report.md"},
                )
            ]
        return "summary ready", None


@pytest.mark.asyncio
async def test_subagent_phase_a_success_closed_loop():
    manager = SubagentManager(task_manager=TaskManager(), llm_provider=_SuccessProvider())

    created = await manager.spawn(
        task="summarize",
        label="s1",
        origin_channel="telegram",
        origin_chat_id="chat-1",
        session_key="telegram:chat-1",
        timeout_seconds=3.0,
    )
    task_id = created["task_id"]

    result = await manager._task_manager.wait_for_task(task_id, timeout=1.0)
    assert result is not None
    assert result.status.value == "completed"

    status = manager.get_task_status(task_id)
    assert status["status"] == "completed"
    assert status["error_code"] is None
    assert status["session_key"] == "telegram:chat-1"
    assert status["origin_chat_id"] == "chat-1"
    assert isinstance(status["latency_ms"], int)


@pytest.mark.asyncio
async def test_subagent_phase_a_failure_closed_loop():
    manager = SubagentManager(task_manager=TaskManager(), llm_provider=_FailureProvider())

    created = await manager.spawn(task="fail me", timeout_seconds=3.0)
    task_id = created["task_id"]

    result = await manager._task_manager.wait_for_task(task_id, timeout=1.0)
    assert result is not None
    assert result.status.value == "failed"

    status = manager.get_task_status(task_id)
    assert status["status"] == "failed"
    assert status["error_code"] == "SUBAGENT_EXECUTION_ERROR"
    assert "boom" in (status["error"] or "")


@pytest.mark.asyncio
async def test_subagent_phase_a_timeout_closed_loop():
    manager = SubagentManager(task_manager=TaskManager(), llm_provider=_SlowProvider(sleep_s=0.2))

    created = await manager.spawn(task="slow", timeout_seconds=0.01)
    task_id = created["task_id"]

    result = await manager._task_manager.wait_for_task(task_id, timeout=1.0)
    assert result is not None
    assert result.status.value == "failed"

    status = manager.get_task_status(task_id)
    assert status["status"] == "timeout"
    assert status["error_code"] == "SUBAGENT_TIMEOUT"


@pytest.mark.asyncio
async def test_subagent_phase_a_cancel_closed_loop():
    manager = SubagentManager(task_manager=TaskManager(), llm_provider=_SlowProvider(sleep_s=1.0))

    created = await manager.spawn(task="cancel me", timeout_seconds=5.0)
    task_id = created["task_id"]

    # Let task enter running state.
    await asyncio.sleep(0.02)
    cancel = await manager.cancel_task(task_id)
    assert cancel["cancelled"] is True

    result = await manager._task_manager.wait_for_task(task_id, timeout=1.0)
    assert result is not None
    assert result.status.value == "cancelled"

    status = manager.get_task_status(task_id)
    assert status["status"] == "cancelled"
    assert status["error_code"] == "TASK_CANCELLED"


@pytest.mark.asyncio
async def test_subagent_can_use_tools_and_spawn_is_excluded():
    provider = _ToolUsingProvider()
    executed: list[ToolCall] = []

    async def _execute(tc: ToolCall) -> ToolResult:
        executed.append(tc)
        return ToolResult(
            tool_call_id=tc.tool_id,
            status="success",
            result="ok",
        )

    manager = SubagentManager(
        task_manager=TaskManager(),
        llm_provider=provider,
        tools_provider=lambda: [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "spawn",
                    "description": "spawn subagent",
                    "parameters": {"type": "object", "properties": {"task": {"type": "string"}}},
                },
            },
        ],
        tool_executor=_execute,
        system_prompt_provider=lambda: "base prompt",
    )

    created = await manager.spawn(task="read and summarize", timeout_seconds=3.0)
    task_id = created["task_id"]

    result = await manager._task_manager.wait_for_task(task_id, timeout=1.0)
    assert result is not None
    assert result.status.value == "completed"
    assert result.result["result"] == "summary ready"

    tool_names = [item.get("function", {}).get("name") for item in provider.last_tools]
    assert "read_file" in tool_names
    assert "spawn" not in tool_names
    assert [tc.name for tc in executed] == ["read_file"]
