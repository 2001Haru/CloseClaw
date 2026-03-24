"""Runtime loop helper service for outbound event emission."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ..types import Message


class RuntimeLoopService:
    """Encapsulates output event payload construction for AgentCore main loop."""

    async def await_auth_or_message(
        self,
        *,
        auth_response_fn: Callable[[str, float], Awaitable[Any]],
        message_input_fn: Callable[[], Awaitable[Any]],
        auth_request_id: str,
        timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Wait for whichever arrives first: auth response or a new user message."""
        auth_task = asyncio.create_task(auth_response_fn(auth_request_id, timeout_seconds))
        msg_task = asyncio.create_task(message_input_fn())

        done, pending = await asyncio.wait(
            [auth_task, msg_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        if msg_task in done:
            return {
                "kind": "new_message",
                "message": msg_task.result(),
            }

        auth_resp = auth_task.result()
        if auth_resp:
            return {
                "kind": "auth_response",
                "auth_response": auth_resp,
            }

        return {"kind": "timeout"}

    async def emit_task_completed(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        task_result: Any,
    ) -> None:
        if isinstance(task_result, dict):
            task_payload = task_result.get("result")
            origin_chat_id = None
            origin_channel = None
            session_key = None
            if isinstance(task_payload, dict):
                origin_chat_id = task_payload.get("origin_chat_id")
                origin_channel = task_payload.get("origin_channel")
                session_key = task_payload.get("session_key")

            payload = {
                "type": "task_completed",
                "task_id": task_result.get("task_id"),
                "status": task_result.get("status"),
                "result": task_result.get("result"),
                "error": task_result.get("error"),
                "origin_channel": origin_channel,
                "session_key": session_key,
            }
            if origin_chat_id not in {None, "", "direct"}:
                payload["_chat_id"] = origin_chat_id
        else:
            # Defensive fallback: preserve signal without crashing the runtime loop.
            payload = {
                "type": "task_completed",
                "task_id": str(task_result),
                "status": "unknown",
                "result": None,
                "error": "Unexpected task result payload type",
            }

        await message_output_fn(
            payload
        )

    async def emit_assistant_message(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        response: str,
        tool_calls: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
    ) -> None:
        await message_output_fn(
            {
                "type": "assistant_message",
                "response": response,
                "tool_calls": tool_calls,
                "tool_results": tool_results,
            }
        )

    async def emit_tool_progress(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        step_id: int,
        tool_name: str,
        status: str,
        target_file: str | None = None,
    ) -> None:
        """Emit minimal channel-visible tool progress event (not conversation history)."""
        payload: dict[str, Any] = {
            "type": "tool_progress",
            "step_id": step_id,
            "tool_name": tool_name,
            "status": status,
        }
        if target_file:
            payload["target_file"] = target_file

        await message_output_fn(payload)

    async def emit_auth_request(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        auth_request_id: str,
        tool_name: str | None,
        description: str | None,
        diff_preview: str | None,
        reason: str | None = None,
        auth_mode: str | None = None,
    ) -> None:
        await message_output_fn(
            {
                "type": "auth_request",
                "auth_request_id": auth_request_id,
                "tool_name": tool_name,
                "description": description,
                "diff_preview": diff_preview,
                "reason": reason,
                "auth_mode": auth_mode,
                "requires_approval": True,
            }
        )

    async def emit_response(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        response: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> None:
        await message_output_fn(
            {
                "type": "response",
                "response": response,
                "tool_calls": tool_calls or [],
                "tool_results": tool_results or [],
            }
        )

    async def emit_error(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        error: str,
    ) -> None:
        await message_output_fn(
            {
                "type": "error",
                "error": error,
            }
        )

    def append_system_notice(
        self,
        *,
        message_history: list[Message],
        channel_type: str,
        suffix: str,
        content: str,
    ) -> None:
        """Append a system notice message for auth lifecycle visibility."""
        message_history.append(
            Message(
                id=f"msg_{datetime.now(timezone.utc).timestamp()}_{suffix}",
                channel_type=channel_type,
                sender_id="system",
                sender_name="System",
                content=content,
            )
        )

    async def emit_resume_payload(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        resume_payload: dict[str, Any],
    ) -> None:
        """Emit resumed assistant payload and optional follow-up auth request."""
        await self.emit_assistant_message(
            message_output_fn,
            response=resume_payload.get("response", "Operation approved."),
            tool_calls=resume_payload.get("tool_calls", []),
            tool_results=resume_payload.get("tool_results", []),
        )

        if resume_payload.get("requires_auth"):
            follow_auth = resume_payload.get("pending_auth", {})
            await self.emit_auth_request(
                message_output_fn,
                auth_request_id=resume_payload.get("auth_request_id"),
                tool_name=follow_auth.get("tool_name"),
                description=follow_auth.get("description"),
                diff_preview=follow_auth.get("diff_preview"),
                reason=follow_auth.get("reason"),
                auth_mode=follow_auth.get("auth_mode"),
            )

    async def emit_auth_approved_resolution(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        message_history: list[Message],
        channel_type: str,
        auth_result: dict[str, Any],
        resume_payload: dict[str, Any] | None,
    ) -> None:
        """Emit outputs for approved auth path, including optional resumed payload."""
        self.append_system_notice(
            message_history=message_history,
            channel_type=channel_type,
            suffix="auth_ok",
            content=(
                "[System] The authorization request was APPROVED. "
                f"Tool Execution Result: {auth_result.get('result', 'OK')}"
            ),
        )

        if resume_payload:
            await self.emit_resume_payload(message_output_fn, resume_payload=resume_payload)
            return

        success_msg = f"Operation approved. Result: {auth_result.get('result', 'OK')}"
        await self.emit_response(message_output_fn, response=success_msg)

    async def emit_auth_rejected_resolution(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        message_history: list[Message],
        channel_type: str,
        auth_result: dict[str, Any],
    ) -> None:
        """Emit outputs for rejected/failed auth path."""
        reason = auth_result.get("error", "Rejected by user")
        self.append_system_notice(
            message_history=message_history,
            channel_type=channel_type,
            suffix="auth_fail",
            content=f"[System] The authorization request was REJECTED or FAILED. Error: {reason}",
        )
        await self.emit_response(
            message_output_fn,
            response=f"Operation {auth_result.get('status', 'rejected')}: {reason}",
        )

    async def emit_auth_timeout_resolution(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        message_history: list[Message],
        channel_type: str,
    ) -> None:
        """Emit outputs for auth-timeout path."""
        self.append_system_notice(
            message_history=message_history,
            channel_type=channel_type,
            suffix="auth_timeout",
            content="[System] The authorization request TIMED OUT. The operation was cancelled.",
        )
        await self.emit_response(
            message_output_fn,
            response="Authorization request timed out. Operation cancelled.",
        )

    async def emit_auth_interrupted_resolution(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        message_history: list[Message],
        channel_type: str,
        new_response: dict[str, Any] | None = None,
    ) -> None:
        """Emit outputs when auth wait is interrupted by a new user message."""
        self.append_system_notice(
            message_history=message_history,
            channel_type=channel_type,
            suffix="cancel",
            content="[System] The previous authorization request was cancelled because the user sent a new message.",
        )

        await self.emit_response(
            message_output_fn,
            response="Auth cancelled by new input.",
        )

        if new_response:
            await self.emit_response(
                message_output_fn,
                response=new_response.get("response", ""),
                tool_calls=new_response.get("tool_calls", []),
                tool_results=new_response.get("tool_results", []),
            )

    async def handle_auth_interruption(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        message_history: list[Message],
        channel_type: str,
        interrupt_message: Any,
        process_message_fn: Callable[[Any], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        """Process an interrupting message and emit cancellation plus immediate response."""
        new_response = None
        if interrupt_message:
            new_response = await process_message_fn(interrupt_message)

        await self.emit_auth_interrupted_resolution(
            message_output_fn,
            message_history=message_history,
            channel_type=channel_type,
            new_response=new_response,
        )
        return new_response

    async def emit_auth_response_resolution(
        self,
        message_output_fn: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        message_history: list[Message],
        channel_type: str,
        approved: bool,
        auth_result: dict[str, Any],
        resume_payload: dict[str, Any] | None,
    ) -> None:
        """Dispatch auth response output path while keeping core state logic unchanged."""
        if approved and auth_result.get("status") == "approved":
            await self.emit_auth_approved_resolution(
                message_output_fn,
                message_history=message_history,
                channel_type=channel_type,
                auth_result=auth_result,
                resume_payload=resume_payload,
            )
            return

        await self.emit_auth_rejected_resolution(
            message_output_fn,
            message_history=message_history,
            channel_type=channel_type,
            auth_result=auth_result,
        )
