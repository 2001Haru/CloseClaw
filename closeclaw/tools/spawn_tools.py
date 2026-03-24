"""Spawn tool for launching background subagent jobs."""

from __future__ import annotations

from typing import Optional

from .base import tool
from ..subagent import get_runtime_subagent_manager
from ..types import ToolType


@tool(
    name="spawn",
    description="Create a background subagent task for parallel research or execution",
    need_auth=False,
    tool_type=ToolType.FILE,
    parameters={
        "task": {
            "type": "string",
            "description": "Detailed subtask for the background subagent, including the path of the file to be operated on and the specific action to be taken.",
        },
        "label": {
            "type": "string",
            "description": "Optional short label for tracking",
        },
        "channel": {
            "type": "string",
            "description": "Origin channel for routing metadata (optional)",
        },
        "to": {
            "type": "string",
            "description": "Origin chat identifier for routing metadata (optional)",
        },
        "path": {
            "type": "string",
            "description": "Compatibility field; ignored when provided",
        },
        "timeout_seconds": {
            "type": "number",
            "description": "Max seconds for subagent execution before timeout (default 120)",
        },
    },
)
async def spawn_impl(
    task: str,
    label: str = "",
    channel: Optional[str] = None,
    to: Optional[str] = None,
    path: Optional[str] = None,
    timeout_seconds: float = 120.0,
    **kwargs,
) -> dict:
    """Launch a background subagent task through TaskManager."""
    _ = (path, kwargs)
    manager = get_runtime_subagent_manager()
    if manager is None:
        raise RuntimeError("Subagent manager is not configured")

    origin_channel = (channel or "cli").strip() or "cli"
    origin_chat_id = (to or "direct").strip() or "direct"
    session_key = f"{origin_channel}:{origin_chat_id}"

    return await manager.spawn(
        task=task,
        label=label or None,
        origin_channel=origin_channel,
        origin_chat_id=origin_chat_id,
        session_key=session_key,
        timeout_seconds=timeout_seconds,
    )


@tool(
    name="task_status",
    description="Check status/result for a background task by task_id",
    need_auth=False,
    tool_type=ToolType.FILE,
    parameters={
        "task_id": {
            "type": "string",
            "description": "Background task identifier like #001",
        },
    },
)
async def task_status_impl(task_id: str) -> dict:
    """Check the status/result of a previously created background task."""
    manager = get_runtime_subagent_manager()
    if manager is None:
        raise RuntimeError("Subagent manager is not configured")

    return manager.get_task_status(task_id)


@tool(
    name="task_cancel",
    description="Cancel a running background task by task_id",
    need_auth=False,
    tool_type=ToolType.FILE,
    parameters={
        "task_id": {
            "type": "string",
            "description": "Background task identifier like #001",
        },
    },
)
async def task_cancel_impl(task_id: str) -> dict:
    """Cancel a running background task."""
    manager = get_runtime_subagent_manager()
    if manager is None:
        raise RuntimeError("Subagent manager is not configured")

    return await manager.cancel_task(task_id)
