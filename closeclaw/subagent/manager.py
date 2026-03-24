"""Background subagent manager powered by TaskManager."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from ..agents.task_manager import TaskManager
from ..types import ToolCall, ToolResult

logger = logging.getLogger(__name__)


class SubagentManager:
    """Manage background subagent jobs via existing TaskManager."""

    _WORKER_TOOL_NAME = "__spawn_subagent_worker__"

    def __init__(
        self,
        task_manager: TaskManager,
        llm_provider: Any,
        tools_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
        tool_executor: Optional[Callable[[ToolCall], Awaitable[ToolResult]]] = None,
        system_prompt_provider: Optional[Callable[[], str]] = None,
        max_steps: int = 6,
    ):
        self._task_manager = task_manager
        self._llm_provider = llm_provider
        self._tools_provider = tools_provider
        self._tool_executor = tool_executor
        self._system_prompt_provider = system_prompt_provider
        self._max_steps = max(1, int(max_steps or 1))
        self._excluded_tool_names = {"spawn"}
        self._task_manager.register_tool_handler(self._WORKER_TOOL_NAME, self._run_subagent_job)

    def _default_system_prompt(self) -> str:
        return (
            "You are a background subagent running inside CloseClaw. You are an upright and excellent assistant of the main worker agent. "
            "Always try your best to complete the task given by the main agent. Complete the task perfectly and Report your final findings explicitly."
            "Never call the spawn tool. Complete the task independently and return concise, actionable output to the main agent."
        )

    def _build_system_prompt(self) -> str:
        if not self._system_prompt_provider:
            return self._default_system_prompt()

        try:
            base = (self._system_prompt_provider() or "").strip()
        except Exception:
            logger.exception("Subagent failed to build inherited system prompt; fallback to default")
            return self._default_system_prompt()

        suffix = (
            "\n\n[SUBAGENT MODE]\n"
            "- You CAN call tools available in function-calling.\n"
            "- Use tools for local files/workspace operations instead of claiming no access.\n"
            "- Never call tool 'spawn'."
        )
        return f"{base}{suffix}" if base else self._default_system_prompt()

    def _filter_tools_for_subagent(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for spec in tools or []:
            fn = spec.get("function") if isinstance(spec, dict) else None
            name = fn.get("name") if isinstance(fn, dict) else None
            if name in self._excluded_tool_names:
                continue
            filtered.append(spec)
        return filtered

    async def _execute_tool_calls(self, tool_calls: list[Any]) -> tuple[list[dict[str, Any]], list[ToolResult]]:
        call_dicts: list[dict[str, Any]] = []
        results: list[ToolResult] = []

        if not self._tool_executor:
            for tc in tool_calls:
                if hasattr(tc, "to_dict"):
                    call_dicts.append(tc.to_dict())
            return call_dicts, results

        for raw_tc in tool_calls:
            tc: Optional[ToolCall] = raw_tc if isinstance(raw_tc, ToolCall) else None
            if tc is None:
                continue

            call_dicts.append(tc.to_dict())
            if tc.name in self._excluded_tool_names:
                results.append(
                    ToolResult(
                        tool_call_id=tc.tool_id,
                        status="blocked",
                        result=None,
                        error="spawn is disabled for subagent execution",
                    )
                )
                continue

            try:
                result = await self._tool_executor(tc)
            except Exception as exc:
                result = ToolResult(
                    tool_call_id=tc.tool_id,
                    status="error",
                    result=None,
                    error=str(exc),
                )
            results.append(result)

        return call_dicts, results

    async def _run_subagent_with_tools(self, task: str) -> tuple[str, list[dict[str, Any]]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._build_system_prompt(),
            },
            {"role": "user", "content": task},
        ]

        tools_for_llm = self._filter_tools_for_subagent(self._tools_provider() if self._tools_provider else [])
        collected_tool_calls: list[dict[str, Any]] = []

        if not tools_for_llm or not self._tool_executor:
            text, tool_calls = await self._llm_provider.generate(messages=messages, tools=[])
            if tool_calls:
                for tc in tool_calls:
                    if hasattr(tc, "to_dict"):
                        collected_tool_calls.append(tc.to_dict())
            return text or "", collected_tool_calls

        for _ in range(self._max_steps):
            text, tool_calls = await self._llm_provider.generate(messages=messages, tools=tools_for_llm)

            if not tool_calls:
                return text or "", collected_tool_calls

            assistant_tool_calls: list[dict[str, Any]] = []
            for tc in tool_calls:
                if hasattr(tc, "to_dict"):
                    assistant_tool_calls.append(
                        {
                            "id": tc.tool_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                    )
            if assistant_tool_calls:
                messages.append({"role": "assistant", "content": text or "", "tool_calls": assistant_tool_calls})

            call_dicts, tool_results = await self._execute_tool_calls(tool_calls)
            collected_tool_calls.extend(call_dicts)

            for tr in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.tool_call_id,
                        "content": json.dumps(tr.to_dict(), ensure_ascii=False),
                    }
                )

        return "Subagent reached max tool steps without final answer.", collected_tool_calls

    async def spawn(
        self,
        *,
        task: str,
        label: Optional[str] = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str = "cli:direct",
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        """Schedule a subagent job and return task metadata."""
        task_id = await self._task_manager.create_task(
            tool_name=self._WORKER_TOOL_NAME,
            arguments={
                "task": task,
                "label": label,
                "origin_channel": origin_channel,
                "origin_chat_id": origin_chat_id,
                "session_key": session_key,
                "timeout_seconds": timeout_seconds,
            },
        )
        logger.info(
            "Spawned background subagent task_id=%s channel=%s chat_id=%s",
            task_id,
            origin_channel,
            origin_chat_id,
        )
        return {
            "status": "task_created",
            "task_id": task_id,
            "message": f"Subagent task created: {task_id}",
            "label": label,
            "origin_channel": origin_channel,
            "origin_chat_id": origin_chat_id,
            "session_key": session_key,
            "timeout_seconds": timeout_seconds,
        }

    async def _run_subagent_job(
        self,
        task: str,
        label: Optional[str] = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str = "cli:direct",
        timeout_seconds: float = 120.0,
        **_: Any,
    ) -> dict[str, Any]:
        """Execute subagent job using an isolated LLM call."""
        started = time.monotonic()
        try:
            text, tool_call_dicts = await asyncio.wait_for(
                self._run_subagent_with_tools(task),
                timeout=timeout_seconds,
            )
            error_code = None
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"SUBAGENT_TIMEOUT: timed out after {timeout_seconds:.2f}s") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "label": label or "",
            "task": task,
            "result": text or "",
            "tool_calls": tool_call_dicts,
            "origin_channel": origin_channel,
            "origin_chat_id": origin_chat_id,
            "session_key": session_key,
            "latency_ms": latency_ms,
            "error_code": error_code,
            "timeout_seconds": timeout_seconds,
        }

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a running background subagent task."""
        cancelled = await self._task_manager.cancel_task(task_id)
        if not cancelled:
            status = self.get_task_status(task_id)
            if status.get("status") == "not_found":
                return {
                    "task_id": task_id,
                    "cancelled": False,
                    "status": "not_found",
                    "error_code": "TASK_NOT_FOUND",
                }
            return {
                "task_id": task_id,
                "cancelled": False,
                "status": status.get("status", "unknown"),
                "error_code": "TASK_NOT_RUNNING",
            }

        return {
            "task_id": task_id,
            "cancelled": True,
            "status": "cancelling",
            "error_code": None,
        }

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Return best-effort status snapshot for a spawned background task."""
        task = self._task_manager.get_status(task_id)
        if task is not None:
            raw_status = getattr(task.status, "value", str(task.status))
            error_code = None
            normalized_status = raw_status

            if raw_status == "failed":
                error_text = task.error or ""
                if "SUBAGENT_TIMEOUT" in error_text:
                    normalized_status = "timeout"
                    error_code = "SUBAGENT_TIMEOUT"
                else:
                    error_code = "SUBAGENT_EXECUTION_ERROR"
            elif raw_status == "cancelled":
                error_code = "TASK_CANCELLED"

            latency_ms: Optional[int] = None
            if task.started_at and task.completed_at:
                latency_ms = int((task.completed_at - task.started_at).total_seconds() * 1000)

            result_payload = task.result if isinstance(task.result, dict) else None
            return {
                "task_id": task.task_id,
                "status": normalized_status,
                "result": task.result,
                "error": task.error,
                "error_code": error_code,
                "session_key": (result_payload or {}).get("session_key"),
                "origin_channel": (result_payload or {}).get("origin_channel"),
                "origin_chat_id": (result_payload or {}).get("origin_chat_id"),
                "latency_ms": latency_ms,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "tool_name": task.tool_name,
            }

        if task_id in self._task_manager.active_tasks:
            return {
                "task_id": task_id,
                "status": "running",
                "result": None,
                "error": None,
                "error_code": None,
                "session_key": None,
                "origin_channel": None,
                "origin_chat_id": None,
                "latency_ms": None,
            }

        return {
            "task_id": task_id,
            "status": "not_found",
            "result": None,
            "error": f"Task not found: {task_id}",
            "error_code": "TASK_NOT_FOUND",
            "session_key": None,
            "origin_channel": None,
            "origin_chat_id": None,
            "latency_ms": None,
        }
