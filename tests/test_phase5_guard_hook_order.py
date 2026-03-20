"""P2-A regression tests for orchestrator guard/hook wiring and execution order."""

from datetime import datetime, timezone

import pytest

from closeclaw.orchestrator.engine import OrchestratorEngine
from closeclaw.orchestrator.guards import GuardDecision
from closeclaw.orchestrator.types import Action, Decision, Observation, RunBudget, RunState
from closeclaw.types import Message


class RecordingGuard:
    def __init__(self, trace):
        self.trace = trace

    async def pre_act(self, state, action):
        self.trace.append("guard.pre_act")
        return None

    async def post_act(self, state, action, observation):
        self.trace.append("guard.post_act")
        return None


class RecordingHook:
    def __init__(self, trace):
        self.trace = trace

    def before_plan(self, state):
        self.trace.append("hook.before_plan")

    def after_observe(self, state, action, observation):
        self.trace.append("hook.after_observe")


class StopInPreActGuard:
    async def pre_act(self, state, action):
        return GuardDecision(
            stop=True,
            reason="blocked_in_pre_act",
            output={
                "response": "blocked by guard",
                "tool_calls": [],
                "tool_results": [],
                "requires_auth": False,
                "memory_flushed": False,
            },
        )

    async def post_act(self, state, action, observation):
        return None


@pytest.mark.asyncio
async def test_engine_guard_hook_execution_order():
    trace = []

    state = RunState(
        run_id="run_order",
        user_message=Message(
            id="u1",
            channel_type="cli",
            sender_id="user",
            sender_name="User",
            content="test",
            timestamp=datetime.now(timezone.utc),
        ),
        budget=RunBudget(max_steps=2),
    )

    async def planner(s):
        trace.append("planner")
        return Action(type="final_answer", payload={"text": "ok"}, reason="test", confidence=1.0)

    async def actor(s, action):
        trace.append("actor")
        return Observation(kind="final_answer", status="success", data={"text": "ok"})

    def observer(s, action, observation):
        trace.append("observer")
        return s

    def decider(s, action, observation):
        trace.append("decider")
        return Decision(stop=True, reason="done", output={"response": "ok"})

    engine = OrchestratorEngine()
    output = await engine.run(
        state,
        planner,
        actor,
        observer,
        decider,
        guards=[RecordingGuard(trace)],
        hooks=[RecordingHook(trace)],
    )

    assert output == {"response": "ok"}
    assert trace == [
        "hook.before_plan",
        "planner",
        "guard.pre_act",
        "actor",
        "guard.post_act",
        "observer",
        "hook.after_observe",
        "decider",
    ]


@pytest.mark.asyncio
async def test_engine_pre_act_guard_can_short_circuit():
    trace = []

    state = RunState(
        run_id="run_short_circuit",
        user_message=Message(
            id="u1",
            channel_type="cli",
            sender_id="user",
            sender_name="User",
            content="test",
            timestamp=datetime.now(timezone.utc),
        ),
        budget=RunBudget(max_steps=2),
    )

    async def planner(s):
        trace.append("planner")
        return Action(type="tool_call", payload={}, reason="test", confidence=1.0)

    async def actor(s, action):
        trace.append("actor")
        return Observation(kind="error", status="error", data={})

    def observer(s, action, observation):
        trace.append("observer")
        return s

    def decider(s, action, observation):
        trace.append("decider")
        return Decision(stop=True, reason="done", output={"response": "should_not_happen"})

    engine = OrchestratorEngine()
    output = await engine.run(
        state,
        planner,
        actor,
        observer,
        decider,
        guards=[StopInPreActGuard()],
        hooks=[],
    )

    assert output["response"] == "blocked by guard"
    assert trace == ["planner"]





