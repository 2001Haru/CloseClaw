"""MCP bridge that projects external tools and registers them to runtime."""

from __future__ import annotations

from typing import Any

from ..services import ToolExecutionService
from .client_pool import MCPClientPool
from .projection import MCPToolProjector


class MCPBridge:
    """Bridge MCP servers into the unified tool execution path."""

    def __init__(self, client_pool: MCPClientPool, projector: MCPToolProjector | None = None) -> None:
        self._client_pool = client_pool
        self._projector = projector or MCPToolProjector()

    async def sync_server_tools(
        self,
        server_id: str,
        tool_execution_service: ToolExecutionService,
        require_healthy: bool = True,
    ) -> list[str]:
        """Project and register all tools from one MCP server."""
        if require_healthy:
            healthy = await self._client_pool.ensure_healthy(server_id)
            if not healthy:
                raise RuntimeError(f"MCP server '{server_id}' is unhealthy and reconnect failed")

        payloads = await self._client_pool.list_tools(server_id)
        registered: list[str] = []

        for payload in payloads:
            projected = self._projector.project(server_id, payload)

            async def _handler(_tool_name: str = projected.tool_name, **kwargs: Any) -> Any:
                return await self._client_pool.call_tool(server_id, _tool_name, kwargs)

            tool_execution_service.register_external_tool(projected.spec, _handler)
            registered.append(projected.spec.name)

        return registered
