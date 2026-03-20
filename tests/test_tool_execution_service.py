"""Tests for unified ToolExecutionService entrypoint."""

import pytest

from closeclaw.middleware import AuthPermissionMiddleware, MiddlewareChain
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
