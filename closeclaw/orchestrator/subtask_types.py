"""Subtask interface contracts for Phase5 P4."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class SubtaskStatus(str, Enum):
    """Lifecycle states for a subtask record."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_SUBTASK_STATUSES = {
    SubtaskStatus.COMPLETED,
    SubtaskStatus.FAILED,
    SubtaskStatus.CANCELLED,
}


class SubtaskErrorCode(str, Enum):
    """Stable error codes for registry operations."""

    NOT_FOUND = "subtask_not_found"
    INVALID_TRANSITION = "subtask_invalid_transition"
    ALREADY_TERMINAL = "subtask_already_terminal"


@dataclass
class SubtaskSpec:
    """Specification used when spawning a new subtask."""

    intent: str
    input_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubtaskHandle:
    """Public handle returned to callers for tracking a subtask."""

    subtask_id: str
    parent_run_id: str


@dataclass
class SubtaskResult:
    """Result payload for a completed/failed/cancelled subtask."""

    status: SubtaskStatus
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class SubtaskRecord:
    """Internal registry record for subtask lifecycle management."""

    subtask_id: str
    parent_run_id: str
    intent: str
    input_payload: dict[str, Any]
    status: SubtaskStatus
    created_at: str
    updated_at: str
    result: Optional[SubtaskResult] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utcnow_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.utcnow().isoformat()
