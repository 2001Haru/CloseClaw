"""MCP client registry and routing helpers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


@dataclass
class MCPClientMetrics:
    """Basic per-server transport counters for observability."""

    list_tools_calls: int = 0
    call_tool_calls: int = 0
    errors: int = 0
    reconnects: int = 0
    last_latency_ms: float = 0.0


class MCPToolClient(Protocol):
    """Minimal client contract for MCP tool providers."""

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions available from this client."""

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a projected MCP tool call."""

    async def health_check(self) -> bool:
        """Optional health check endpoint for MCP transport clients."""

    async def reconnect(self) -> None:
        """Optional reconnect hook for MCP transport clients."""


class MCPClientPool:
    """Stores MCP clients by server id and routes calls."""

    def __init__(self, reconnect_attempts: int = 1) -> None:
        self._clients: dict[str, MCPToolClient] = {}
        self._client_factories: dict[str, Callable[[], Awaitable[MCPToolClient] | MCPToolClient]] = {}
        self._metrics: dict[str, MCPClientMetrics] = {}
        self._reconnect_attempts = reconnect_attempts

    def register(
        self,
        server_id: str,
        client: MCPToolClient,
        factory: Callable[[], Awaitable[MCPToolClient] | MCPToolClient] | None = None,
    ) -> None:
        self._clients[server_id] = client
        self._metrics.setdefault(server_id, MCPClientMetrics())
        if factory is not None:
            self._client_factories[server_id] = factory

    def register_factory(
        self,
        server_id: str,
        factory: Callable[[], Awaitable[MCPToolClient] | MCPToolClient],
    ) -> None:
        self._client_factories[server_id] = factory

    def unregister(self, server_id: str) -> None:
        self._clients.pop(server_id, None)
        self._client_factories.pop(server_id, None)
        self._metrics.pop(server_id, None)

    def get(self, server_id: str) -> MCPToolClient | None:
        return self._clients.get(server_id)

    def get_metrics(self, server_id: str) -> MCPClientMetrics | None:
        return self._metrics.get(server_id)

    async def ensure_healthy(self, server_id: str) -> bool:
        """Ensure client is healthy; attempt reconnect when unhealthy."""
        if await self._is_healthy(server_id):
            return True

        for _ in range(max(1, self._reconnect_attempts)):
            if not await self._try_reconnect(server_id):
                continue
            if await self._is_healthy(server_id):
                return True
        return False

    async def health_check(self, server_id: str) -> dict[str, Any]:
        """Return health snapshot for one MCP server."""
        healthy = await self._is_healthy(server_id)
        metrics = self._metrics.get(server_id) or MCPClientMetrics()
        return {
            "server_id": server_id,
            "healthy": healthy,
            "metrics": {
                "list_tools_calls": metrics.list_tools_calls,
                "call_tool_calls": metrics.call_tool_calls,
                "errors": metrics.errors,
                "reconnects": metrics.reconnects,
                "last_latency_ms": metrics.last_latency_ms,
            },
        }

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Return health snapshot for all registered MCP servers."""
        result: dict[str, dict[str, Any]] = {}
        for server_id in list(self._clients.keys()):
            result[server_id] = await self.health_check(server_id)
        return result

    async def list_tools(self, server_id: str) -> list[dict[str, Any]]:
        metrics = self._metrics.setdefault(server_id, MCPClientMetrics())
        metrics.list_tools_calls += 1
        result = await self._execute_with_recovery(server_id, "list_tools")
        if not isinstance(result, list):
            raise RuntimeError(f"MCP server '{server_id}' list_tools returned invalid payload")
        return result

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        metrics = self._metrics.setdefault(server_id, MCPClientMetrics())
        metrics.call_tool_calls += 1
        return await self._execute_with_recovery(server_id, "call_tool", tool_name, arguments)

    async def _execute_with_recovery(self, server_id: str, method_name: str, *args: Any) -> Any:
        client = self.get(server_id)
        if not client:
            raise ValueError(f"MCP server '{server_id}' not registered")

        started = time.perf_counter()
        try:
            method = getattr(client, method_name)
            result = await method(*args)
            self._record_latency(server_id, started, method_name, ok=True)
            return result
        except Exception as first_error:
            self._metrics.setdefault(server_id, MCPClientMetrics()).errors += 1
            logger.warning(
                "MCP %s failed for server=%s; attempting reconnect once: %s",
                method_name,
                server_id,
                first_error,
            )

            if await self._try_reconnect(server_id):
                client_after = self.get(server_id)
                if client_after is not None:
                    try:
                        method = getattr(client_after, method_name)
                        result = await method(*args)
                        self._record_latency(server_id, started, method_name, ok=True)
                        return result
                    except Exception as second_error:
                        self._metrics.setdefault(server_id, MCPClientMetrics()).errors += 1
                        self._record_latency(server_id, started, method_name, ok=False)
                        raise RuntimeError(
                            f"MCP call failed after reconnect for server '{server_id}': {second_error}"
                        ) from second_error

            self._record_latency(server_id, started, method_name, ok=False)
            raise

    async def _is_healthy(self, server_id: str) -> bool:
        client = self.get(server_id)
        if not client:
            return False

        health_check = getattr(client, "health_check", None)
        if callable(health_check):
            try:
                return bool(await health_check())
            except Exception:
                return False

        try:
            await client.list_tools()
            return True
        except Exception:
            return False

    async def _try_reconnect(self, server_id: str) -> bool:
        client = self.get(server_id)
        if not client:
            return False

        reconnect = getattr(client, "reconnect", None)
        if callable(reconnect):
            try:
                await reconnect()
                self._metrics.setdefault(server_id, MCPClientMetrics()).reconnects += 1
                logger.info("MCP reconnect successful via client hook for server=%s", server_id)
                return True
            except Exception as exc:
                logger.warning("MCP reconnect hook failed for server=%s: %s", server_id, exc)

        factory = self._client_factories.get(server_id)
        if not factory:
            return False

        try:
            created = factory()
            next_client = await created if hasattr(created, "__await__") else created
            self._clients[server_id] = next_client
            self._metrics.setdefault(server_id, MCPClientMetrics()).reconnects += 1
            logger.info("MCP reconnect successful via factory for server=%s", server_id)
            return True
        except Exception as exc:
            logger.warning("MCP reconnect factory failed for server=%s: %s", server_id, exc)
            return False

    def _record_latency(self, server_id: str, started: float, method_name: str, ok: bool) -> None:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        metrics = self._metrics.setdefault(server_id, MCPClientMetrics())
        metrics.last_latency_ms = elapsed_ms
        logger.info(
            "[MCP_METRIC] server=%s method=%s ok=%s latency_ms=%.2f",
            server_id,
            method_name,
            ok,
            elapsed_ms,
        )

    async def close_all(self) -> None:
        """Close all registered clients that expose async close()."""
        for server_id, client in list(self._clients.items()):
            close_fn = getattr(client, "close", None)
            if not callable(close_fn):
                continue
            try:
                await close_fn()
            except Exception as exc:
                logger.debug("Ignoring MCP client close error for server=%s: %s", server_id, exc)
