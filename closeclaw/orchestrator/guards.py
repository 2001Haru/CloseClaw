"""Guard contracts and baseline guard implementations for Phase5 P2."""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

from .types import Action, Observation, RunState


@dataclass
class GuardDecision:
    """Optional early-stop decision emitted by a guard."""

    stop: bool
    reason: str
    output: Optional[dict] = None


class OrchestratorGuard(Protocol):
    """Guard hook points around ACT step."""

    async def pre_act(self, state: RunState, action: Action) -> Optional[GuardDecision]:
        ...

    async def post_act(self, state: RunState, action: Action, observation: Observation) -> Optional[GuardDecision]:
        ...


class PreActBudgetGuard:
    """Hard-stop guard to prevent execution past configured step budget."""

    async def pre_act(self, state: RunState, action: Action) -> Optional[GuardDecision]:
        if state.step_id >= state.budget.max_steps:
            return GuardDecision(
                stop=True,
                reason="budget_exhausted_pre_act",
                output={
                    "response": "I couldn't complete this request within the current step budget.",
                    "tool_calls": [tc.to_dict() for tc in state.tool_calls],
                    "tool_results": [tr.to_dict() for tr in state.tool_results],
                    "requires_auth": False,
                    "memory_flushed": False,
                    "decision": "budget_exhausted",
                },
            )
        return None

    async def post_act(self, state: RunState, action: Action, observation: Observation) -> Optional[GuardDecision]:
        return None


class PreActContextGuard:
    """P2 skeleton context guard.

    Actual threshold/flush logic will be migrated from AgentCore in P2-B.
    """

    def __init__(
        self,
        pre_act_callback: Optional[Callable[[RunState, Action], Awaitable[Optional[GuardDecision]]]] = None,
    ):
        self._pre_act_callback = pre_act_callback

    async def pre_act(self, state: RunState, action: Action) -> Optional[GuardDecision]:
        state.metadata["guard_path"] = True
        if self._pre_act_callback is not None:
            return await self._pre_act_callback(state, action)
        return None

    async def post_act(self, state: RunState, action: Action, observation: Observation) -> Optional[GuardDecision]:
        return None


class PostActSafetyGuard:
    """P2 skeleton for post-action safety checks (transcript/tool hygiene)."""

    async def pre_act(self, state: RunState, action: Action) -> Optional[GuardDecision]:
        return None

    async def post_act(self, state: RunState, action: Action, observation: Observation) -> Optional[GuardDecision]:
        return None

