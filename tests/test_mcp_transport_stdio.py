"""Tests for MCP STDIO transport client behavior."""

import json
import sys
import textwrap

import pytest

from closeclaw.mcp.transport import MCPStdioClient


MOCK_STDIO_SERVER_CODE = textwrap.dedent(r"""
import json
import sys

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    req = json.loads(raw)
    method = req.get("method")
    req_id = req.get("id")

    if method == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": [
                {
                    "name": "stdio_echo",
                    "description": "Echo tool via stdio",
                    "input_schema": {"text": {"type": "string"}},
                    "need_auth": False,
                    "tool_type": "websearch",
                }
            ],
        }
    elif method == "tools/call":
        params = req.get("params", {})
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tool": params.get("name"),
                "arguments": params.get("arguments", {}),
            },
        }
    else:
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": "unknown method",
        }

    sys.stdout.write(json.dumps(resp) + "\\n")
    sys.stdout.flush()
""")


@pytest.mark.asyncio
async def test_stdio_client_list_and_call_tool(tmp_path):
    script_path = tmp_path / "mock_mcp_stdio_server.py"
    script_path.write_text(MOCK_STDIO_SERVER_CODE, encoding="utf-8")

    client = MCPStdioClient(
        command=sys.executable,
        args=["-u", str(script_path)],
        timeout_seconds=5.0,
    )

    async with client:
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "stdio_echo"

        result = await client.call_tool("stdio_echo", {"text": "hi"})
        assert result["tool"] == "stdio_echo"
        assert result["arguments"]["text"] == "hi"
