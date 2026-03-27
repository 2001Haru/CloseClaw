"""Unified tool execution entrypoint.

This service normalizes tool definitions into ToolSpecV2 and executes tool calls
through a single runtime path.
"""

from __future__ import annotations

import copy
import inspect
import time
from typing import Any, Callable, Optional

from ..compatibility import NativeAdapter, ToolSpecV2
from ..middleware import MiddlewareChain
from ..tools.adaptation import ToolAdaptationLayer
from ..types import Session, Tool, ToolCall, ToolResult, ToolType


class ToolExecutionService:
    """Single entrypoint for tool normalization and execution."""

    def __init__(
        self,
        tools: dict[str, Tool],
        middleware_chain: Optional[MiddlewareChain],
        tool_adaptation_layer: ToolAdaptationLayer,
        session_getter: Callable[[], Optional[Session]],
        task_manager_getter: Callable[[], Any],
    ) -> None:
        self._tools = tools
        self._external_specs: dict[str, ToolSpecV2] = {}
        self._external_handlers: dict[str, Callable[..., Any]] = {}
        self._middleware_chain = middleware_chain
        self._tool_adaptation_layer = tool_adaptation_layer
        self._session_getter = session_getter
        self._task_manager_getter = task_manager_getter

    def register_external_tool(self, spec: ToolSpecV2, handler: Callable[..., Any]) -> None:
        """Register an external tool (e.g. projected from MCP/OpenClaw)."""
        self._external_specs[spec.name] = spec
        self._external_handlers[spec.name] = handler

    def unregister_external_tool(self, tool_name: str) -> None:
        """Unregister an external tool by name."""
        self._external_specs.pop(tool_name, None)
        self._external_handlers.pop(tool_name, None)

    def list_external_specs(self) -> list[ToolSpecV2]:
        """Return all currently registered external tool specs."""
        return list(self._external_specs.values())

    def update_middleware_chain(self, chain: Optional[MiddlewareChain]) -> None:
        """Update middleware chain without recreating the service."""
        self._middleware_chain = chain

    def normalize_to_v2(self, tool: Tool | ToolSpecV2) -> ToolSpecV2:
        """Normalize legacy/new tool schema into ToolSpecV2."""
        if isinstance(tool, ToolSpecV2):
            return tool
        return NativeAdapter.to_toolspec_v2(tool)

    async def execute_tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Execute tool call through unified policy and adaptation pipeline."""
        tool = self._tools.get(tool_call.name)
        external_spec = self._external_specs.get(tool_call.name)
        is_external = tool is None and external_spec is not None
        permission_context: dict[str, Any] = {}

        if not tool and not external_spec:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=f"Tool '{tool_call.name}' not found",
            )

        spec = external_spec if external_spec else self.normalize_to_v2(tool)
        permission_tool = tool if tool else self._build_permission_tool_from_spec(spec)

        session = self._session_getter()
        user_id = session.user_id if session else None

        if self._middleware_chain:
            check_permission = self._middleware_chain.check_permission
            base_kwargs: dict[str, Any] = {
                "tool": permission_tool,
                "arguments": tool_call.arguments,
                "session": session,
                "user_id": user_id,
            }
            extra_kwargs: dict[str, Any] = {
                "raw_arguments": copy.deepcopy(tool_call.arguments),
                "tool_source": spec.source,
                "tool_source_ref": spec.source_ref,
                "tool_type": spec.tool_type,
            }

            supports_var_kwargs = False
            accepted_params: set[str] = set()
            try:
                signature = inspect.signature(check_permission)
                for param in signature.parameters.values():
                    if param.kind == inspect.Parameter.VAR_KEYWORD:
                        supports_var_kwargs = True
                    elif param.kind in {
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    }:
                        accepted_params.add(param.name)
            except (TypeError, ValueError):
                supports_var_kwargs = True

            if supports_var_kwargs:
                call_kwargs = {**base_kwargs, **extra_kwargs}
            else:
                call_kwargs = {k: v for k, v in base_kwargs.items() if k in accepted_params}
                call_kwargs.update({k: v for k, v in extra_kwargs.items() if k in accepted_params})

            auth_result = await check_permission(**call_kwargs)

            if auth_result["status"] == "block":
                return ToolResult(
                    tool_call_id=tool_call.tool_id,
                    status="blocked",
                    result=None,
                    error=auth_result.get("reason", "Blocked by safety filter"),
                    metadata={
                        "source": spec.source,
                        "tool_type": spec.tool_type,
                        "need_auth": spec.need_auth,
                    },
                )

            if auth_result["status"] == "requires_auth":
                result = ToolResult(
                    tool_call_id=tool_call.tool_id,
                    status="auth_required",
                    result=None,
                )
                auth_result["toolspec_v2"] = spec.to_dict()
                result.metadata = auth_result
                return result

            if auth_result.get("status") == "allow":
                permission_context = {
                    "auth_mode": auth_result.get("auth_mode"),
                    "reason": auth_result.get("reason"),
                    "reason_code": auth_result.get("reason_code"),
                    "guardian_comment": auth_result.get("guardian_comment"),
                }

        execution_args = dict(tool_call.arguments) if isinstance(tool_call.arguments, dict) else {}
        execution_args.pop("_force_execute", None)
        sanitized_call = ToolCall(
            tool_id=tool_call.tool_id,
            name=tool_call.name,
            arguments=execution_args,
        )

        if is_external:
            result = await self._execute_external_tool_call(sanitized_call, spec)
            result.metadata = {
                **(result.metadata or {}),
                **{k: v for k, v in permission_context.items() if v is not None},
            }
            return result

        result = await self._tool_adaptation_layer.execute_tool_call(
            tool_call=sanitized_call,
            available_tools=self._tools,
            task_manager=self._task_manager_getter(),
            direct_executor=self._execute_tool_directly,
        )

        result.metadata = {
            **(result.metadata or {}),
            **{k: v for k, v in permission_context.items() if v is not None},
            "source": spec.source,
            "tool_type": spec.tool_type,
            "need_auth": spec.need_auth,
        }
        return result

    async def execute_authorized_request(self, auth_payload: dict[str, Any]) -> Any:
        """Execute a previously authorized request with full middleware re-validation."""
        tool_name = auth_payload.get("tool_name")
        if not tool_name:
            raise ValueError("Missing tool_name in auth payload")

        raw_args = auth_payload.get("arguments", {})
        arguments = dict(raw_args) if isinstance(raw_args, dict) else {}
        arguments["_force_execute"] = True
        replay_call = ToolCall(
            tool_id=f"auth_replay_{int(time.time() * 1000)}",
            name=str(tool_name),
            arguments=arguments,
        )
        replay_result = await self.execute_tool_call(replay_call)

        if replay_result.status in {"success", "task_created"}:
            return replay_result.result
        if replay_result.status == "blocked":
            raise PermissionError(replay_result.error or "Blocked during authorization replay")
        if replay_result.status == "auth_required":
            raise PermissionError("Authorization replay still requires approval")
        raise RuntimeError(replay_result.error or f"Authorization replay failed: {replay_result.status}")

    async def _execute_tool_directly(self, tool_call: ToolCall) -> ToolResult:
        """Direct tool execution for sync/fast calls routed by adaptation layer."""
        tool = self._tools.get(tool_call.name)
        if not tool:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=f"Tool '{tool_call.name}' not found",
            )

        try:
            result = await tool.handler(**tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="success",
                result=result,
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=str(exc),
            )

    async def _execute_external_tool_call(self, tool_call: ToolCall, spec: ToolSpecV2) -> ToolResult:
        """Execute projected external tool directly (M3 baseline)."""
        handler = self._external_handlers.get(tool_call.name)
        if not handler:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=f"External tool '{tool_call.name}' not found",
            )

        try:
            result = await handler(**tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="success",
                result=result,
                metadata={
                    "routing": "direct",
                    "source": spec.source,
                    "tool_type": spec.tool_type,
                    "need_auth": spec.need_auth,
                    "source_ref": spec.source_ref,
                },
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=str(exc),
                metadata={
                    "source": spec.source,
                    "tool_type": spec.tool_type,
                    "need_auth": spec.need_auth,
                    "source_ref": spec.source_ref,
                },
            )

    def _build_permission_tool_from_spec(self, spec: ToolSpecV2) -> Tool:
        """Create a Tool shim so middleware can evaluate external tools."""
        return Tool(
            name=spec.name,
            description=spec.description,
            type=self._tool_type_from_str(spec.tool_type),
            need_auth=spec.need_auth,
            parameters=spec.input_schema,
        )

    def _tool_type_from_str(self, tool_type: str) -> ToolType:
        normalized = (tool_type or "").strip().lower()
        if normalized in {ToolType.FILE.value, "filesystem", "fs", "document", "doc"}:
            return ToolType.FILE
        if normalized in {ToolType.SHELL.value, "exec", "execute", "command", "cmd", "terminal"}:
            return ToolType.SHELL
        if normalized in {ToolType.WEBSEARCH.value, "web", "network", "http", "https", "url", "search", "api"}:
            return ToolType.WEBSEARCH
        # Conservative fallback for unknown external tool types.
        return ToolType.SHELL
