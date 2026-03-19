"""Planning and progress policies for Phase5 P1."""

from dataclasses import dataclass
from typing import Optional

from .types import Action, RunState


@dataclass
class ProgressPolicy:
    """Tiny no-progress tracker used by P1 decider logic."""

    no_progress_limit: int = 2

    def should_stop(self, state: RunState) -> bool:
        stagnation = state.metadata.get("stagnation_count", 0)
        return stagnation >= self.no_progress_limit


class PlanPolicy:
    """Planning override hooks for orchestrator steps.

    Current behavior intentionally does not force post-tool finalization.
    This preserves iterative PLAN->ACT loops so the model can decide whether
    another tool call is needed in the same turn.
    """

    def next_action_after_observation(self, state: RunState) -> Optional[Action]:
        return None
