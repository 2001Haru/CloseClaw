"""HTTP transport client for MCP servers.

Implements a small JSON-RPC style request/response flow for tools/list and
tools/call methods, with timeout and retry handling.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import httpx


class MCPHttpClient:
    """HTTP transport facade for MCP JSON-RPC requests."""

    def __init__(
        self,
        base_url: str,
        endpoint: str = "/mcp",
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._id_counter = itertools.count(1)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "MCPHttpClient":
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
        request_id = next(self._id_counter)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(self.endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("MCP response is not a JSON object")

                if "error" in data and data["error"] is not None:
                    raise RuntimeError(f"MCP error response: {data['error']}")

                if "result" not in data:
                    raise RuntimeError("MCP response missing 'result'")

                return data["result"]
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))

        raise RuntimeError(f"MCP HTTP request failed after retries: {last_error}")
