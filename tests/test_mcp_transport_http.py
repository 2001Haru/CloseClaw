"""Tests for MCP HTTP transport client behavior."""

import json

import httpx
import pytest

from closeclaw.mcp.transport import MCPHttpClient


@pytest.mark.asyncio
async def test_http_client_list_tools_success():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["method"] == "tools/list"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": [
                    {
                        "name": "echo",
                        "description": "Echo tool",
                        "input_schema": {"text": {"type": "string"}},
                        "need_auth": False,
                        "tool_type": "websearch",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as injected:
        client = MCPHttpClient(base_url="http://mcp.test", client=injected)
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"


@pytest.mark.asyncio
async def test_http_client_call_tool_with_retry():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(500, json={"error": "temporary"})

        payload = json.loads(request.content.decode("utf-8"))
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "echo"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "ok": True,
                    "received": payload["params"]["arguments"],
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as injected:
        client = MCPHttpClient(
            base_url="http://mcp.test",
            client=injected,
            max_retries=1,
            retry_backoff_seconds=0.0,
        )
        result = await client.call_tool("echo", {"text": "hi"})

    assert attempts["count"] == 2
    assert result["ok"] is True
    assert result["received"]["text"] == "hi"
