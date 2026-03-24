"""Cron scheduling tools."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from .base import tool
from ..cron import CronSchedule, get_runtime_cron_service
from ..types import ToolType

logger = logging.getLogger(__name__)


def _parse_wake_time_to_ms(wake_time: str) -> int:
    """Parse wake time string into unix epoch milliseconds.

    Supported formats:
    - ISO datetime, e.g. 2026-03-22T08:30:00+08:00
    - ISO datetime with Z suffix, e.g. 2026-03-22T00:30:00Z
    - Unix timestamp (seconds or milliseconds)
    """
    raw = (wake_time or "").strip()
    if not raw:
        raise ValueError("wake_time is required")

    if raw.isdigit():
        ts = int(raw)
        return ts if ts > 10**12 else ts * 1000

    iso_raw = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso_raw)
    except ValueError as exc:
        raise ValueError(
            "Invalid wake_time format. Use ISO datetime (e.g. 2026-03-22T08:30:00+08:00) "
            "or unix timestamp"
        ) from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return int(dt.timestamp() * 1000)


@tool(
    name="call_cron",
    description="Schedule a one-time wake-up at a fixed time (no auth required, requires explicit target channel)",
    need_auth=False,
    tool_type=ToolType.FILE,
    parameters={
        "wake_time": {
            "type": "string",
            "description": "Wake time: ISO datetime or unix timestamp"
        },
        "message": {
            "type": "string",
            "description": "Wake-up message payload"
        },
        "channel": {
            "type": "string",
            "description": "Target channel for wake-up delivery (cli/telegram/feishu)"
        },
        "to": {
            "type": "string",
            "description": "Optional target recipient/chat id for channel delivery"
        },
        "deliver": {
            "type": "boolean",
            "description": "Whether this job is intended for channel delivery"
        }
    }
)
async def call_cron_impl(
    wake_time: str,
    message: str = "wake_agent",
    channel: str = "",
    to: str = "",
    deliver: bool = True,
    path: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create a one-time cron wake-up job on the active CronService."""
    _ = (path, kwargs)

    cron_service = get_runtime_cron_service()
    if cron_service is None:
        raise RuntimeError("CronService is not available in current runtime")
    if not cron_service.enabled:
        raise RuntimeError("CronService is disabled by configuration")

    at_ms = _parse_wake_time_to_ms(wake_time)
    now_ms = int(time.time() * 1000)
    if at_ms <= now_ms:
        raise ValueError("wake_time must be in the future")

    target_channel = (channel or "").strip().lower()
    if not target_channel:
        raise ValueError("channel is required (cli/telegram/feishu)")

    allowed_channels = {"cli", "telegram", "feishu"}
    if target_channel not in allowed_channels:
        raise ValueError(f"unsupported channel: {target_channel}. allowed={sorted(allowed_channels)}")

    target_to = (to or "").strip()
    if not target_to:
        # Keep API simple: selecting channel is enough for scheduling.
        # Non-CLI channels may reuse runtime routing context if available.
        target_to = "direct"

    job_id = f"wake_{int(time.time() * 1000)}"
    schedule = CronSchedule(kind="at", at_ms=at_ms, tz=cron_service.default_timezone)
    job = cron_service.add_job(
        job_id=job_id,
        schedule=schedule,
        message=message,
        deliver=deliver,
        channel=target_channel,
        to=target_to,
    )

    logger.info(
        "Scheduled wake cron job id=%s at_ms=%s channel=%s to=%s",
        job.id,
        at_ms,
        target_channel,
        target_to,
    )
    return {
        "scheduled": True,
        "job_id": job.id,
        "wake_time_ms": at_ms,
        "message": message,
        "channel": target_channel,
        "to": target_to,
        "deliver": bool(deliver),
        "next_run_at_ms": job.state.next_run_at_ms,
    }
