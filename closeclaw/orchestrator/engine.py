"""Minimal PLAN/ACT/OBSERVE/DECIDE engine for Phase5 P1."""

from typing import Awaitable, Callable

from .types import Action, Decision, Observation, RunState

Planner = Callable[[RunState], Awaitable[Action]]
Actor = Callable[[RunState, Action], Awaitable[Observation]]
Observer = Callable[[RunState, Action, Observation], RunState]
Decider = Callable[[RunState, Action, Observation], Decision]


class OrchestratorEngine:
    """Single-loop orchestrator with explicit step transitions."""

    async def run(
        self,
        state: RunState,
        planner: Planner,
        actor: Actor,
        observer: Observer,
        decider: Decider,
    ) -> dict:
        while state.step_id < state.budget.max_steps:
            action = await planner(state)
            state.actions.append(action)

            observation = await actor(state, action)
            state.observations.append(observation)

            state = observer(state, action, observation)
            decision = decider(state, action, observation)
            if decision.stop:
                return decision.output or {}

            state.step_id += 1

        return {
            "response": "I couldn't complete this request within the current step budget.",
            "tool_calls": [tc.to_dict() for tc in state.tool_calls],
            "tool_results": [tr.to_dict() for tr in state.tool_results],
            "requires_auth": False,
            "memory_flushed": False,
            "decision": "budget_exhausted",
        }
