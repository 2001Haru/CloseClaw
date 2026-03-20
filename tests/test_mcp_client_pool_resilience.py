"""Tests for MCPClientPool health checks, reconnect, and metrics."""

import pytest

from closeclaw.mcp import MCPClientPool


class _HealthyClient:
    async def health_check(self) -> bool:
        return True

    async def list_tools(self):
        return [{"name": "x"}]

    async def call_tool(self, tool_name, arguments):
        return {"ok": True, "tool": tool_name, "arguments": arguments}


class _RecoverableClient:
    def __init__(self):
        self.connected = False

    async def health_check(self) -> bool:
        return self.connected

    async def reconnect(self) -> None:
        self.connected = True

    async def list_tools(self):
        if not self.connected:
            raise RuntimeError("disconnected")
        return [{"name": "after_reconnect"}]

    async def call_tool(self, tool_name, arguments):
        if not self.connected:
            raise RuntimeError("disconnected")
        return {"tool": tool_name, "arguments": arguments}


class _FailThenSucceedClient:
    def __init__(self):
        self.calls = 0

    async def list_tools(self):
        return [{"name": "echo"}]

    async def call_tool(self, tool_name, arguments):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient failure")
        return {"tool": tool_name, "arguments": arguments}


@pytest.mark.asyncio
async def test_health_check_uses_client_hook():
    pool = MCPClientPool()
    pool.register("s1", _HealthyClient())

    status = await pool.health_check("s1")
    assert status["healthy"] is True


@pytest.mark.asyncio
async def test_ensure_healthy_reconnects_via_client_hook():
    pool = MCPClientPool()
    client = _RecoverableClient()
    pool.register("s2", client)

    ok = await pool.ensure_healthy("s2")
    assert ok is True

    tools = await pool.list_tools("s2")
    assert tools[0]["name"] == "after_reconnect"


@pytest.mark.asyncio
async def test_call_tool_retry_after_factory_replacement():
    pool = MCPClientPool()
    failing = _FailThenSucceedClient()

    # Factory returns healthy replacement after first failure.
    class _HealthyReplacement:
        async def list_tools(self):
            return [{"name": "echo"}]

        async def call_tool(self, tool_name, arguments):
            return {"tool": tool_name, "arguments": arguments, "replacement": True}

    pool.register("s3", failing, factory=lambda: _HealthyReplacement())

    result = await pool.call_tool("s3", "echo", {"text": "hi"})
    assert result["tool"] == "echo"
    assert result["replacement"] is True

    metrics = pool.get_metrics("s3")
    assert metrics is not None
    assert metrics.call_tool_calls == 1
    assert metrics.errors >= 1
    assert metrics.reconnects >= 1
