"""Tests for MCP bridge registration and execution integration."""

import json

import httpx
import pytest

from closeclaw.mcp import MCPBridge, MCPClientPool
from closeclaw.mcp.transport import MCPHttpClient
from closeclaw.services import ToolExecutionService
from closeclaw.tools.adaptation import ToolAdaptationLayer
from closeclaw.types import Session, ToolCall


class _FakeMCPClient:
    async def list_tools(self):
        return [
            {
                "name": "mcp_echo",
                "description": "Echo from MCP",
                "input_schema": {"text": {"type": "string"}},
                "need_auth": False,
                "tool_type": "websearch",
            }
        ]

    async def call_tool(self, tool_name, arguments):
        return {"tool": tool_name, "arguments": arguments}


class _UnhealthyMCPClient:
    async def health_check(self):
        return False

    async def list_tools(self):
        raise RuntimeError("down")

    async def call_tool(self, tool_name, arguments):
        raise RuntimeError("down")


@pytest.mark.asyncio
async def test_mcp_bridge_registers_and_executes_projected_tool():
    pool = MCPClientPool()
    pool.register("mock_server", _FakeMCPClient())

    session = Session(session_id="s1", user_id="u1", channel_type="cli")
    service = ToolExecutionService(
        tools={},
        middleware_chain=None,
        tool_adaptation_layer=ToolAdaptationLayer(),
        session_getter=lambda: session,
        task_manager_getter=lambda: None,
    )

    bridge = MCPBridge(pool)
    names = await bridge.sync_server_tools("mock_server", service)
    assert names == ["mcp_echo"]

    result = await service.execute_tool_call(
        ToolCall(tool_id="tc1", name="mcp_echo", arguments={"text": "hello"})
    )
    assert result.status == "success"
    assert result.metadata.get("source") == "mcp"
    assert result.result["tool"] == "mcp_echo"


@pytest.mark.asyncio
async def test_mcp_bridge_with_http_transport_client_end_to_end():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        method = payload.get("method")
        request_id = payload.get("id")

        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "name": "http_echo",
                            "description": "Echo from http transport",
                            "input_schema": {"text": {"type": "string"}},
                            "need_auth": False,
                            "tool_type": "websearch",
                        }
                    ],
                },
            )

        if method == "tools/call":
            params = payload.get("params", {})
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tool": params.get("name"),
                        "arguments": params.get("arguments", {}),
                    },
                },
            )

        return httpx.Response(400, json={"jsonrpc": "2.0", "id": request_id, "error": "unknown method"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as injected:
        pool = MCPClientPool()
        pool.register("http_server", MCPHttpClient(base_url="http://mcp.test", client=injected))

        session = Session(session_id="s2", user_id="u2", channel_type="cli")
        service = ToolExecutionService(
            tools={},
            middleware_chain=None,
            tool_adaptation_layer=ToolAdaptationLayer(),
            session_getter=lambda: session,
            task_manager_getter=lambda: None,
        )

        bridge = MCPBridge(pool)
        names = await bridge.sync_server_tools("http_server", service)
        assert names == ["http_echo"]

        result = await service.execute_tool_call(
            ToolCall(tool_id="tc-http", name="http_echo", arguments={"text": "hello"})
        )

    assert result.status == "success"
    assert result.metadata.get("source") == "mcp"
    assert result.result["tool"] == "http_echo"
    assert result.result["arguments"]["text"] == "hello"


@pytest.mark.asyncio
async def test_mcp_bridge_sync_fails_when_server_unhealthy():
    pool = MCPClientPool()
    pool.register("bad_server", _UnhealthyMCPClient())

    session = Session(session_id="s3", user_id="u3", channel_type="cli")
    service = ToolExecutionService(
        tools={},
        middleware_chain=None,
        tool_adaptation_layer=ToolAdaptationLayer(),
        session_getter=lambda: session,
        task_manager_getter=lambda: None,
    )

    bridge = MCPBridge(pool)
    with pytest.raises(RuntimeError, match="unhealthy"):
        await bridge.sync_server_tools("bad_server", service)
