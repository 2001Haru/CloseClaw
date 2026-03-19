"""Core data contracts for Phase5 orchestrator."""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from ..types import Message, ToolCall, ToolResult

ActionType = Literal["tool_call", "final_answer", "plan_update"]
ObservationKind = Literal["tool_result", "final_answer", "plan_update", "error"]


@dataclass
class Action:
    """A single orchestrator action in the MVP action space."""

    type: ActionType
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0


@dataclass
class Observation:
    """Normalized output from ACT step."""

    kind: ObservationKind
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class Decision:
    """Decision after OBSERVE step."""

    stop: bool
    reason: str
    output: Optional[dict[str, Any]] = None


@dataclass
class RunBudget:
    """Execution budget for a single orchestrator run."""

    max_steps: int = 6


@dataclass
class RunState:
    """Mutable state for a single user-message run."""

    run_id: str
    user_message: Message
    budget: RunBudget
    step_id: int = 0
    actions: list[Action] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    pending_actions: list[ToolCall] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
