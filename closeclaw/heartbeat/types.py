"""Types for heartbeat decision and execution summary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HeartbeatDecision:
    """Normalized heartbeat decision."""

    action: str
    tasks: str = ""
    reason: str = ""


@dataclass
class HeartbeatTickResult:
    """Single heartbeat tick execution result."""

    action: str
    tasks: str = ""
    status: str = "skipped"
    reason: str = ""
    result: Any = None
    target_channel: str = ""
    target_chat_id: str = ""
    duration_ms: int = 0
