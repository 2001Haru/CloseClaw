"""Hook contracts and baseline hook implementations for Phase5 P2."""

from typing import Protocol

from .types import Action, Observation, RunState


class OrchestratorHook(Protocol):
    """Hook points before PLAN and after OBSERVE merge."""

    def before_plan(self, state: RunState) -> None:
        ...

    def after_observe(self, state: RunState, action: Action, observation: Observation) -> None:
        ...


class BeforePlanHook:
    """Baseline pre-plan hook marker for P2-A rollout."""

    def before_plan(self, state: RunState) -> None:
        state.metadata["hook_path"] = True

    def after_observe(self, state: RunState, action: Action, observation: Observation) -> None:
        return None


class AfterObserveHook:
    """Baseline after-observe hook placeholder for telemetry/enrichment."""

    def before_plan(self, state: RunState) -> None:
        return None

    def after_observe(self, state: RunState, action: Action, observation: Observation) -> None:
        return None
