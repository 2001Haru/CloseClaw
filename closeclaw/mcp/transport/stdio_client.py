"""STDIO transport client for local MCP servers.

Uses newline-delimited JSON-RPC messages over subprocess stdin/stdout.
"""

from __future__ import annotations

import asyncio
import json
import itertools
from typing import Any


class MCPStdioClient:
    """Minimal stdio transport facade for local MCP servers."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.command = command
        self.args = args or []
        self.timeout_seconds = timeout_seconds
        self._id_counter = itertools.count(1)
        self._request_lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return

        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def close(self) -> None:
        if self._process is None:
            return

        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
                await self._process.stdin.wait_closed()
            except Exception:
                pass

        if self._process.stdout is not None:
            try:
                await asyncio.wait_for(self._process.stdout.read(), timeout=1.0)
            except Exception:
                pass

        if self._process.stderr is not None:
            try:
                await asyncio.wait_for(self._process.stderr.read(), timeout=1.0)
            except Exception:
                pass

        self._process = None

    async def __aenter__(self) -> "MCPStdioClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._jsonrpc_request("tools/list", params={})
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            tools = result.get("tools")
            if isinstance(tools, list):
                return tools
        raise RuntimeError("Invalid MCP tools/list response: expected list of tools")

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not tool_name:
            raise ValueError("tool_name must be non-empty")

        return await self._jsonrpc_request(
            "tools/call",
            params={
                "name": tool_name,
                "arguments": arguments,
            },
        )

    async def _jsonrpc_request(self, method: str, params: dict[str, Any]) -> Any:
        await self.start()
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("MCP stdio process is not available")

        payload = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": method,
            "params": params,
        }

        async with self._request_lock:
            serialized = json.dumps(payload, ensure_ascii=False) + "\n"
            self._process.stdin.write(serialized.encode("utf-8"))
            await self._process.stdin.drain()

            try:
                raw = await asyncio.wait_for(
                    self._process.stdout.readuntil(b"\n"),
                    timeout=self.timeout_seconds,
                )
            except asyncio.IncompleteReadError as exc:
                raw = exc.partial
                if not raw:
                    raise RuntimeError("MCP stdio server closed output stream") from exc
            except asyncio.TimeoutError as exc:
                stderr_text = ""
                if self._process.stderr is not None:
                    try:
                        stderr_bytes = await asyncio.wait_for(
                            self._process.stderr.read(),
                            timeout=0.2,
                        )
                        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                    except Exception:
                        stderr_text = "<stderr unavailable>"
                raise RuntimeError(
                    f"Timed out waiting for MCP stdio response. stderr={stderr_text!r}"
                ) from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid MCP stdio JSON response: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError("MCP stdio response is not a JSON object")
        if "error" in data and data["error"] is not None:
            raise RuntimeError(f"MCP stdio error response: {data['error']}")
        if "result" not in data:
            raise RuntimeError("MCP stdio response missing 'result'")

        return data["result"]
