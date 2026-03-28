"""Tests for unified ToolExecutionService entrypoint."""

import pytest
from pathlib import Path

from closeclaw.middleware import AuthPermissionMiddleware, MiddlewareChain, PathSandbox
from closeclaw.safety import SecurityMode
from closeclaw.services import ToolExecutionService
from closeclaw.tools.adaptation import ToolAdaptationLayer
from closeclaw.types import Tool, ToolCall, ToolType


@pytest.fixture
def service_context(sample_session):
    tools = {}
    chain = MiddlewareChain([AuthPermissionMiddleware(default_need_auth=False)])
    adapter = ToolAdaptationLayer()

    service = ToolExecutionService(
        tools=tools,
        middleware_chain=chain,
        tool_adaptation_layer=adapter,
        session_getter=lambda: sample_session,
        task_manager_getter=lambda: None,
    )
    return service, tools


@pytest.mark.asyncio
async def test_normalize_to_v2_native(service_context):
    service, tools = service_context

    async def read_handler(**kwargs):
        return "ok"

    tool = Tool(
        name="read_file",
        description="Read file",
        type=ToolType.FILE,
        need_auth=False,
        handler=read_handler,
        parameters={"path": {"type": "string"}},
    )
    tools[tool.name] = tool

    spec = service.normalize_to_v2(tool)
    assert spec.name == "read_file"
    assert spec.need_auth is False
    assert spec.source == "native"


@pytest.mark.asyncio
async def test_execute_missing_tool_returns_error(service_context):
    service, _ = service_context

    result = await service.execute_tool_call(
        ToolCall(tool_id="tc_1", name="not_exists", arguments={})
    )
    assert result.status == "error"
    assert "not found" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_sensitive_tool_requires_auth(service_context):
    service, tools = service_context

    async def delete_handler(**kwargs):
        return "deleted"

    tool = Tool(
        name="delete_file",
        description="Delete file",
        type=ToolType.FILE,
        need_auth=True,
        handler=delete_handler,
        parameters={"path": {"type": "string"}},
    )
    tools[tool.name] = tool

    result = await service.execute_tool_call(
        ToolCall(tool_id="tc_2", name="delete_file", arguments={"path": "a.txt"})
    )
    assert result.status == "auth_required"
    assert result.metadata.get("toolspec_v2", {}).get("name") == "delete_file"


@pytest.mark.asyncio
async def test_execute_safe_tool_success(service_context):
    service, tools = service_context

    async def read_handler(**kwargs):
        return "content"

    tool = Tool(
        name="read_file",
        description="Read file",
        type=ToolType.FILE,
        need_auth=False,
        handler=read_handler,
        parameters={"path": {"type": "string"}},
    )
    tools[tool.name] = tool

    result = await service.execute_tool_call(
        ToolCall(tool_id="tc_3", name="read_file", arguments={"path": "a.txt"})
    )
    assert result.status == "success"
    assert result.result == "content"
    assert result.metadata.get("source") == "native"


@pytest.mark.asyncio
async def test_consensus_allow_metadata_propagates_to_tool_result(sample_session):
    class _Decision:
        approved = True
        reason_code = "SAFE"
        comment = "looks good"

    class _Guardian:
        async def review(self, _payload):
            return _Decision()

    async def run_handler(**kwargs):
        return "done"

    tools = {
        "run_cmd": Tool(
            name="run_cmd",
            description="Run command",
            type=ToolType.SHELL,
            need_auth=True,
            handler=run_handler,
            parameters={"command": {"type": "string"}},
        )
    }
    chain = MiddlewareChain(
        [
            AuthPermissionMiddleware(
                default_need_auth=False,
                security_mode=SecurityMode.CONSENSUS,
                consensus_guardian=_Guardian(),
            )
        ]
    )
    service = ToolExecutionService(
        tools=tools,
        middleware_chain=chain,
        tool_adaptation_layer=ToolAdaptationLayer(),
        session_getter=lambda: sample_session,
        task_manager_getter=lambda: None,
    )

    result = await service.execute_tool_call(
        ToolCall(tool_id="tc_consensus", name="run_cmd", arguments={"command": "echo hi"})
    )

    assert result.status == "success"
    assert result.metadata.get("auth_mode") == "consensus"
    assert result.metadata.get("reason_code") == "SAFE"
    assert result.metadata.get("guardian_comment") == "looks good"


@pytest.mark.asyncio
async def test_execute_authorized_request_rechecks_middleware_and_sanitizes_force_arg(sample_session, temp_workspace):
    captured = {}

    async def write_handler(path: str):
        captured["path"] = path
        return "ok"

    tools = {
        "write_file": Tool(
            name="write_file",
            description="Write file",
            type=ToolType.FILE,
            need_auth=True,
            handler=write_handler,
            parameters={"path": {"type": "string"}},
        )
    }

    chain = MiddlewareChain(
        [
            PathSandbox(temp_workspace),
            AuthPermissionMiddleware(default_need_auth=False),
        ]
    )
    service = ToolExecutionService(
        tools=tools,
        middleware_chain=chain,
        tool_adaptation_layer=ToolAdaptationLayer(),
        session_getter=lambda: sample_session,
        task_manager_getter=lambda: None,
    )

    with pytest.raises(PermissionError):
        await service.execute_authorized_request(
            {
                "tool_name": "write_file",
                "arguments": {"path": "/etc/passwd"},
            }
        )

    result = await service.execute_authorized_request(
        {
            "tool_name": "write_file",
            "arguments": {"path": "ok.txt"},
        }
    )
    assert result == "ok"
    assert "path" in captured
    assert Path(captured["path"]).resolve().is_relative_to(Path(temp_workspace).resolve())


@pytest.mark.asyncio
async def test_execute_tool_call_blocks_external_force_execute_injection(service_context):
    service, tools = service_context

    async def delete_handler(**kwargs):
        return "deleted"

    tools["delete_file"] = Tool(
        name="delete_file",
        description="Delete file",
        type=ToolType.FILE,
        need_auth=True,
        handler=delete_handler,
        parameters={"path": {"type": "string"}},
    )

    result = await service.execute_tool_call(
        ToolCall(
            tool_id="tc_force_inject",
            name="delete_file",
            arguments={"path": "a.txt", "_force_execute": True},
        )
    )
    assert result.status == "blocked"
    assert "_force_execute" in (result.error or "")
