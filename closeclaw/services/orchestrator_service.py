"""Orchestrator service for Phase5 PLAN-ACT-OBSERVE-DECIDE loop.

Extracted from AgentCore to decouple the orchestrator turn logic from the
agent facade.  All AgentCore state access is mediated through explicit
callbacks passed via ``AgentFacade``.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol

from ..types import (
    AgentState, Message, ToolCall, ToolResult,
)
from ..orchestrator import (
    Action,
    Decision,
    Observation,
    OrchestratorEngine,
    PlanPolicy,
    ProgressPolicy,
    RunBudget,
    RunState,
    TodoStore,
    AfterObserveHook,
    BeforePlanHook,
    PostActSafetyGuard,
    PreActBudgetGuard,
    PreActContextGuard,
)

logger = logging.getLogger(__name__)


class AgentFacade(Protocol):
    """Minimal interface that OrchestratorService needs from AgentCore."""

    agent_id: str
    state: AgentState
    _phase5_auth_paused_runs: dict[str, RunState]
    _runtime_message_output_fn: Optional[Any]
    _critical_trim_notice_pending: bool
    current_session: Optional[Any]
    message_history: list[Message]

    def _format_conversation_for_llm(self) -> list[dict[str, Any]]: ...
    def _format_tools_for_llm(self) -> list[dict[str, Any]]: ...
    async def _process_tool_call(self, tool_call: ToolCall) -> ToolResult: ...
    async def _maybe_trigger_memory_flush_before_planning(self) -> None: ...


class OrchestratorService:
    """Orchestrates a single user turn through the PLAN-ACT-OBSERVE-DECIDE loop.

    This service encapsulates all four closures (planner, actor, observer,
    decider), the synthesis / fallback helpers, and the auth-resume flow.
    """

    def __init__(
        self,
        *,
        config: Any,
        planning_service: Any,
        context_service: Any,
        runtime_loop_service: Any,
        progress_policy: ProgressPolicy,
        plan_policy: PlanPolicy,
        orchestrator_engine: OrchestratorEngine,
        orchestrator_guards: list[Any],
        orchestrator_hooks: list[Any],
        memory_flush_coordinator: Any,
    ):
        self.config = config
        self.planning_service = planning_service
        self.context_service = context_service
        self.runtime_loop_service = runtime_loop_service
        self.progress_policy = progress_policy
        self.plan_policy = plan_policy
        self.orchestrator_engine = orchestrator_engine
        self.orchestrator_guards = orchestrator_guards
        self.orchestrator_hooks = orchestrator_hooks
        self.memory_flush_coordinator = memory_flush_coordinator

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run_turn(
        self,
        message: Message,
        agent: AgentFacade,
    ) -> dict[str, Any]:
        """Execute a full PLAN→ACT→OBSERVE→DECIDE turn for *message*."""
        agent.message_history.append(message)

        run_state = RunState(
            run_id=f"run_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            user_message=message,
            budget=RunBudget(max_steps=self._max_steps()),
            metadata={
                "stagnation_count": 0,
                "force_replan": False,
                "stop_after_replan": False,
                "todo_store": TodoStore(),
            },
        )

        # Build the four closures, each capturing *agent* and *self*.
        planner = self._make_planner(agent, run_state)
        actor = self._make_actor(agent)
        observer = self._make_observer()
        decider = self._make_decider(agent)

        output = await self.orchestrator_engine.run(
            run_state,
            planner,
            actor,
            observer,
            decider,
            guards=self.orchestrator_guards,
            hooks=self.orchestrator_hooks,
        )

        if agent._critical_trim_notice_pending:
            warning_text = (
                "[CONTEXT WARNING] Context reached CRITICAL. "
                "History was hard-trimmed to the latest 10 rounds to recover stability."
            )
            current_response = output.get("response") or ""
            output["response"] = f"{warning_text}\n\n{current_response}" if current_response else warning_text
            agent._critical_trim_notice_pending = False

        agent.message_history.append(Message(
            id=f"msg_{datetime.now(timezone.utc).timestamp()}",
            channel_type=agent.current_session.channel_type if agent.current_session else "unknown",
            sender_id=agent.agent_id,
            sender_name="Agent",
            content=output.get("response", "No response generated."),
            tool_calls=run_state.tool_calls or None,
            tool_results=run_state.tool_results or None,
        ))

        return output

    async def resume_after_auth(
        self,
        auth_request_id: str,
        auth_result: dict[str, Any],
        agent: AgentFacade,
    ) -> Optional[dict[str, Any]]:
        """Resume a paused run after an auth decision and auto-finalize output."""
        state = agent._phase5_auth_paused_runs.pop(auth_request_id, None)
        if not state:
            return None

        if auth_result.get("status") != "approved":
            return {
                "response": "Authorization was rejected, so the pending action was not executed.",
                "tool_calls": [tc.to_dict() for tc in state.tool_calls],
                "tool_results": [tr.to_dict() for tr in state.tool_results],
                "requires_auth": False,
                "memory_flushed": False,
            }

        # Replace the auth_required placeholder with concrete success.
        for tr in reversed(state.tool_results):
            if tr.status == "auth_required":
                tr.status = "success"
                tr.result = auth_result.get("result")
                tr.error = None
                break

        # Continue queued actions until another auth gate.
        while state.pending_actions:
            tool_call = state.pending_actions.pop(0)
            tool_result = await agent._process_tool_call(tool_call)
            state.tool_calls.append(tool_call)
            state.tool_results.append(tool_result)

            if tool_result.status == "auth_required":
                pending_auth = tool_result.metadata
                next_auth_id = pending_auth.get("auth_request_id") if pending_auth else None
                if next_auth_id:
                    agent._phase5_auth_paused_runs[next_auth_id] = state
                agent.state = AgentState.WAITING_FOR_AUTH
                return {
                    "assistant_message": "Previous action was approved. Another protected action needs authorization.",
                    "response": "Previous action was approved. Another protected action needs authorization.",
                    "tool_calls": [tc.to_dict() for tc in state.tool_calls],
                    "tool_results": [tr.to_dict() for tr in state.tool_results],
                    "requires_auth": True,
                    "auth_request_id": next_auth_id,
                    "pending_auth": pending_auth,
                    "memory_flushed": False,
                }

        final_text = await self._synthesize_answer(state, agent)
        return {
            "response": final_text,
            "tool_calls": [tc.to_dict() for tc in state.tool_calls],
            "tool_results": [tr.to_dict() for tr in state.tool_results],
            "requires_auth": False,
            "memory_flushed": False,
        }

    # ------------------------------------------------------------------
    # Context guard callback
    # ------------------------------------------------------------------

    async def pre_act_context_guard(
        self,
        state: RunState,
        action: Action,
        agent: AgentFacade,
    ) -> Optional[Any]:
        """P2-B: run-scoped context pressure hook invoked by PreActContextGuard."""
        if state.metadata.get("context_guard_checked", False):
            return None

        state.metadata["context_guard_checked"] = True
        prior_session_id = self.memory_flush_coordinator.last_flush_session_id
        await agent._maybe_trigger_memory_flush_before_planning()

        new_session_id = self.memory_flush_coordinator.last_flush_session_id
        if new_session_id and new_session_id != prior_session_id:
            state.metadata["flush_triggered_in_run"] = True

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _max_steps(self) -> int:
        orchestrator = self.config.metadata.get(
            "orchestrator", self.config.metadata.get("phase5", {})
        )
        max_steps = orchestrator.get("max_steps", 6)
        try:
            return max(1, int(max_steps))
        except (TypeError, ValueError):
            return 6

    def _make_planner(self, agent: AgentFacade, initial_state: RunState):
        """Return an async planner closure."""

        async def planner(state: RunState) -> Action:
            if state.pending_actions:
                return Action(
                    type="tool_call",
                    payload={"tool_call": state.pending_actions.pop(0)},
                    reason="pending_action_queue",
                    confidence=1.0,
                )

            planned = self.plan_policy.next_action_after_observation(state)
            if planned is not None:
                return planned

            messages_for_llm = self._build_messages_for_planner(state, agent)
            tools_for_llm = agent._format_tools_for_llm()
            llm_response, tool_calls = await self.planning_service.generate_plan_or_answer(
                messages=messages_for_llm,
                tools=tools_for_llm,
                temperature=self.config.temperature,
            )

            if tool_calls:
                if len(tool_calls) > 1:
                    state.pending_actions.extend(tool_calls[1:])
                    logger.info("Phase5 P1.5 queued %d additional tool action(s)", len(tool_calls) - 1)
                state.metadata["initial_llm_response"] = llm_response or ""
                return Action(
                    type="tool_call",
                    payload={"tool_call": tool_calls[0]},
                    reason="llm_requested_tool",
                    confidence=1.0,
                )

            return Action(
                type="final_answer",
                payload={"text": llm_response or "I couldn't produce a useful answer."},
                reason="llm_direct_answer",
                confidence=1.0,
            )

        return planner

    def _make_actor(self, agent: AgentFacade):
        """Return an async actor closure."""

        async def actor(state: RunState, action: Action) -> Observation:
            if action.type == "tool_call":
                tool_call: ToolCall = action.payload["tool_call"]
                tool_result = await agent._process_tool_call(tool_call)
                await self._emit_tool_progress_event(
                    agent,
                    step_id=state.step_id,
                    tool_call=tool_call,
                    tool_result=tool_result,
                )
                return Observation(
                    kind="tool_result",
                    status=tool_result.status,
                    data={"tool_call": tool_call, "tool_result": tool_result},
                    error=tool_result.error,
                )

            if action.type == "final_answer":
                text = action.payload.get("text")
                if action.payload.get("mode") == "synthesize_from_observations":
                    text = await self._synthesize_answer(state, agent)
                return Observation(
                    kind="final_answer",
                    status="success",
                    data={"text": text or self.fallback_summary(state)},
                )

            return Observation(
                kind="plan_update",
                status="success",
                data={
                    "text": action.payload.get("text") or json.dumps(action.payload, ensure_ascii=False),
                    "payload": action.payload,
                },
            )

        return actor

    def _make_observer(self):
        """Return an observer closure."""

        def observer(state: RunState, action: Action, observation: Observation) -> RunState:
            if observation.kind == "tool_result":
                state.tool_calls.append(observation.data["tool_call"])
                state.tool_results.append(observation.data["tool_result"])
            return state

        return observer

    def _make_decider(self, agent: AgentFacade):
        """Return a decider closure."""

        def decider(state: RunState, action: Action, observation: Observation) -> Decision:
            if observation.kind == "tool_result":
                tool_result: ToolResult = observation.data["tool_result"]
                if tool_result.status == "auth_required":
                    agent.state = AgentState.WAITING_FOR_AUTH
                    pending_auth = tool_result.metadata
                    auth_request_id = pending_auth.get("auth_request_id") if pending_auth else None
                    if auth_request_id:
                        agent._phase5_auth_paused_runs[auth_request_id] = state

                    assistant_message = state.metadata.get("initial_llm_response") or self.fallback_summary(state)
                    return Decision(
                        stop=True,
                        reason="auth_required",
                        output={
                            "assistant_message": assistant_message,
                            "response": assistant_message,
                            "tool_calls": [tc.to_dict() for tc in state.tool_calls],
                            "tool_results": [tr.to_dict() for tr in state.tool_results],
                            "requires_auth": True,
                            "auth_request_id": auth_request_id,
                            "pending_auth": pending_auth,
                            "memory_flushed": False,
                        },
                    )

                if tool_result.status == "success":
                    state.metadata["stagnation_count"] = 0
                elif tool_result.status in {"error", "blocked", "task_created"}:
                    state.metadata["stagnation_count"] = int(state.metadata.get("stagnation_count", 0)) + 1
                    if self.progress_policy.should_stop(state):
                        state.metadata["replan_payload"] = self._build_structured_plan_update_payload(
                            state,
                            reason="no_progress_limit_reached",
                        )
                        state.metadata["force_replan"] = True
                        state.metadata["stop_after_replan"] = True
                        return Decision(stop=False, reason="force_replan")

                if state.pending_actions:
                    return Decision(stop=False, reason="continue_pending_actions")

                if tool_result.status in {"error", "blocked", "task_created"}:
                    return Decision(stop=False, reason="tool_finished_with_non_success")

                return Decision(stop=False, reason="tool_success_continue")

            if observation.kind in {"final_answer", "plan_update"}:
                if observation.kind == "plan_update" and state.metadata.get("stop_after_replan", False):
                    state.metadata["stop_after_replan"] = False
                    return Decision(
                        stop=True,
                        reason="no_progress_limit_reached",
                        output={
                            "response": "I stopped this run after generating a recovery plan because no meaningful progress was made.",
                            "plan_update": observation.data.get("payload", {}),
                            "tool_calls": [tc.to_dict() for tc in state.tool_calls],
                            "tool_results": [tr.to_dict() for tr in state.tool_results],
                            "requires_auth": False,
                            "memory_flushed": False,
                            "decision": "no_progress_limit_reached",
                        },
                    )

                response_text = observation.data.get("text") or self.fallback_summary(state)
                return Decision(
                    stop=True,
                    reason="completed",
                    output={
                        "response": response_text,
                        "tool_calls": [tc.to_dict() for tc in state.tool_calls],
                        "tool_results": [tr.to_dict() for tr in state.tool_results],
                        "requires_auth": False,
                        "memory_flushed": False,
                    },
                )

            return Decision(stop=False, reason="continue")

        return decider

    # ------------------------------------------------------------------
    # Synthesis / fallback
    # ------------------------------------------------------------------

    async def _synthesize_answer(self, state: RunState, agent: AgentFacade) -> str:
        """Generate same-turn final answer from observed tool results."""
        synthesis_messages = agent._format_conversation_for_llm()

        for tc, tr in zip(state.tool_calls, state.tool_results):
            content = self.context_service.serialize_tool_result(tr)
            synthesis_messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": tc.tool_id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }],
            })
            synthesis_messages.append({
                "role": "tool",
                "tool_call_id": tc.tool_id,
                "content": content,
            })

        synthesis_messages.append({
            "role": "user",
            "content": (
                "[Phase5] Based on the conversation and tool results above, provide the final user-facing answer now. "
                "Do not call any additional tools."
            ),
        })

        try:
            response_text = await self.planning_service.synthesize_answer(
                messages=synthesis_messages,
                temperature=self.config.temperature,
            )
            if response_text and response_text.strip():
                return response_text.strip()
        except Exception as exc:
            logger.error("Phase5 synthesis failed: %s", exc)

        return self.fallback_summary(state)

    @staticmethod
    def fallback_summary(state: RunState) -> str:
        """Fallback summary to avoid placeholder-only responses."""
        if not state.tool_results:
            return "I could not produce a reliable answer in this turn."

        successful = [tr for tr in state.tool_results if tr.status == "success"]
        if successful:
            top = successful[-1].result
            if isinstance(top, str) and top.strip():
                return top
            return json.dumps(top)

        top = state.tool_results[-1]
        return top.error or f"Tool execution finished with status: {top.status}"

    # ------------------------------------------------------------------
    # Message building helpers
    # ------------------------------------------------------------------

    def _build_messages_for_planner(
        self, state: RunState, agent: AgentFacade
    ) -> list[dict[str, Any]]:
        messages_for_llm = agent._format_conversation_for_llm()

        for tc, tr in zip(state.tool_calls, state.tool_results):
            messages_for_llm.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": tc.tool_id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }],
            })
            messages_for_llm.append({
                "role": "tool",
                "tool_call_id": tc.tool_id,
                "content": self.context_service.serialize_tool_result(tr),
            })

        return messages_for_llm

    @staticmethod
    def _build_structured_plan_update_payload(state: RunState, reason: str) -> dict[str, Any]:
        todo_store = state.metadata.get("todo_store")
        if isinstance(todo_store, TodoStore):
            todo_store.upsert(
                item_id="replan_root",
                title="Recover progress after repeated non-success tool results",
                status="blocked",
                source_step=state.step_id,
            )
            todo_snapshot = todo_store.export_snapshot()
        else:
            todo_snapshot = []

        return {
            "goal": "Recover progress and prevent repeated no-op tool loops",
            "current_step": f"replan_required:{reason}",
            "remaining_steps": [
                "inspect_latest_tool_errors",
                "choose_alternative_action_or_parameters",
                "execute_single_high-confidence_step",
            ],
            "done_criteria": [
                "at least one successful tool_result",
                "stagnation_count reset to 0",
            ],
            "risk": [
                "tool unavailable or blocked repeatedly",
                "context pressure causing low-quality plans",
            ],
            "todo_snapshot": todo_snapshot,
        }

    # ------------------------------------------------------------------
    # Progress event emission
    # ------------------------------------------------------------------

    async def _emit_tool_progress_event(
        self,
        agent: AgentFacade,
        *,
        step_id: int,
        tool_call: ToolCall,
        tool_result: ToolResult,
    ) -> None:
        """Emit per-tool progress to active channel."""
        if not agent._runtime_message_output_fn:
            return

        target_file = self._extract_progress_target_file(tool_call, tool_result)
        status = "success" if tool_result.status in {"success", "task_created"} else "fail"

        try:
            await self.runtime_loop_service.emit_tool_progress(
                agent._runtime_message_output_fn,
                step_id=step_id,
                tool_name=tool_call.name,
                status=status,
                target_file=target_file,
            )
        except Exception as exc:
            logger.exception("Failed to emit tool progress event: %s", exc)

    @staticmethod
    def _extract_progress_target_file(tool_call: ToolCall, tool_result: ToolResult) -> str | None:
        """Best-effort extraction of a file path target for progress visibility."""
        path_keys = ("filePath", "path", "file", "filename", "target", "uri")

        def _pick_path(value: Any) -> str | None:
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        for key in path_keys:
            candidate = _pick_path(args.get(key))
            if candidate:
                return candidate

        result = tool_result.result if isinstance(tool_result.result, dict) else {}
        for key in path_keys:
            candidate = _pick_path(result.get(key))
            if candidate:
                return candidate

        return None
