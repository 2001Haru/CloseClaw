"""Agent core implementation."""

import logging
import os
import json
from typing import Any, Optional, Protocol
from datetime import datetime, timezone

from ..types import (
    Agent, AgentConfig, Session, Tool, Message,
    AgentState, ToolCall, ToolResult, ToolType
)
from ..middleware import MiddlewareChain
from ..tools.adaptation import ToolAdaptationLayer
from ..safety import AuditLogger
from ..context import ContextManager, MessageCompactor
from ..memory import MemoryFlushCoordinator, MemoryFlushSession, MemoryManager
from ..orchestrator import (
    Action,
    AfterObserveHook,
    BeforePlanHook,
    Decision,
    Observation,
    OrchestratorEngine,
    PlanPolicy,
    ProgressPolicy,
    PostActSafetyGuard,
    PreActBudgetGuard,
    PreActContextGuard,
    RunBudget,
    RunState,
    TodoStore,
)
from ..services import AuthService, ContextService, PlanningService, RuntimeLoopService, StateService, ToolExecutionService, ToolSchemaService

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    """Protocol for LLM providers."""
    
    async def generate(self, 
                      messages: list[dict[str, str]],
                      tools: list[dict[str, Any]],
                      **kwargs: Any) -> tuple[str, Optional[list[ToolCall]]]:
        """Generate response from LLM.
        
        Returns: (response_text, tool_calls)
        """
        ...


class AgentCore:
    """Simplified Agent core loop engine.
    
    Replaces complex event streams with a synchronous loop that:
    - Takes user input
    - Calls LLM
    - Executes tool calls
    - Applies permission checks via middleware
    - Requests HITL confirmation for tools with need_auth=True
    - Returns results back to user
    
    Code target: < 500 lines
    """

    COMPACT_MEMORY_MAX_CHARS = 3000

    def __init__(self,
                 agent_id: str,
                 llm_provider: LLMProvider,
                 config: AgentConfig,
                 workspace_root: str,
                 admin_user_id: Optional[str] = None,
                 state_file: Optional[str] = None):
        """Initialize agent core.
        
        Args:
            agent_id: Unique agent identifier
            llm_provider: LLM implementation
            config: Agent configuration
            workspace_root: Root directory for file operations (sandboxing)
            admin_user_id: User ID authorized for HITL approval
            state_file: Path to state persistence file (relative to workspace_root)
        """
        self.agent_id = agent_id
        self.llm_provider = llm_provider
        self.config = config
        self.workspace_root = workspace_root
        self.admin_user_id = admin_user_id
        self.state_file = state_file
        
        self.state = AgentState.IDLE
        self.current_session: Optional[Session] = None
        self.tools: dict[str, Tool] = {}
        self.message_history: list[Message] = []
        self.compact_memory_snapshot: Optional[str] = None
        self.pending_auth_requests: dict[str, Any] = {}  # auth_id -> request
        self._memory_flush_in_progress = False
        self._critical_trim_notice_pending = False
        self.middleware_chain: Optional[MiddlewareChain] = None
        self.tool_adaptation_layer = ToolAdaptationLayer()  # Phase 2: Tool routing
        
        # Phase 3.5: Transcript Repair firewall - audit logger init
        audit_log_path = os.path.join(workspace_root, "audit.log")
        self.audit_logger = AuditLogger(log_file=audit_log_path)
        
        # Phase 4: Context Management - Token counting and message compaction
        # Support legacy/simple config objects that may not have context_management / llm attrs
        cm = getattr(config, "context_management", None)
        if cm is None:
            # Build default using values on config or hard-coded defaults
            from ..types.models import ContextManagementSettings
            cm = ContextManagementSettings(
                max_tokens=getattr(config, "max_context_tokens", 100000),
                warning_threshold=getattr(config, "warning_threshold", 0.75),
                critical_threshold=getattr(config, "critical_threshold", 0.95),
                summarize_window=getattr(config, "summarize_window", 50),
                active_window=getattr(config, "active_window", 10),
                chunk_size=getattr(config, "chunk_size", 5000),
            )
        llm_settings = getattr(config, "llm", None)
        if llm_settings is None:
            # Fallback to any simple model attribute or default
            class _LLMShim:
                model = getattr(config, "model", "gpt-4")
            llm_settings = _LLMShim()
    
        self.context_manager = ContextManager(
            max_tokens=cm.max_tokens,
            warning_threshold=cm.warning_threshold,
            critical_threshold=cm.critical_threshold,
            summarize_window=cm.summarize_window,
            active_window=cm.active_window,
            model=llm_settings.model
        )

        self.message_compactor = MessageCompactor(
            summarize_window=cm.summarize_window,
            active_window=cm.active_window,
            chunk_size=cm.chunk_size
        )

        # Phase 4 Step 2: Memory flush manager (WARNING threshold trigger)
        self.memory_flush_session = MemoryFlushSession(workspace_root=workspace_root)
        self.memory_flush_coordinator = MemoryFlushCoordinator(self.memory_flush_session)
        
        # Phase 4 Step 3: Memory Manager - SQLite + Vector Search
        self.memory_manager = MemoryManager(workspace_root=workspace_root)

        # Phase 5: Single-loop orchestrator (MVP)
        self.orchestrator_engine = OrchestratorEngine()
        self.plan_policy = PlanPolicy()
        phase5_cfg = self.config.metadata.get("phase5", {})
        self.progress_policy = ProgressPolicy(no_progress_limit=int(phase5_cfg.get("no_progress_limit", 2)))
        self.orchestrator_guards = [
            PreActBudgetGuard(),
            PreActContextGuard(pre_act_callback=self._phase5_pre_act_context_guard),
            PostActSafetyGuard(),
        ]
        self.orchestrator_hooks = [
            BeforePlanHook(),
            AfterObserveHook(),
        ]
        self._phase5_auth_paused_runs: dict[str, RunState] = {}
        self.tool_execution_service = ToolExecutionService(
            tools=self.tools,
            middleware_chain=self.middleware_chain,
            tool_adaptation_layer=self.tool_adaptation_layer,
            session_getter=lambda: self.current_session,
            task_manager_getter=lambda: getattr(self, "task_manager", None),
        )
        self.planning_service = PlanningService(llm_provider=self.llm_provider)
        self.auth_service = AuthService(
            pending_auth_requests=self.pending_auth_requests,
            admin_user_id=self.admin_user_id,
        )
        self.tool_schema_service = ToolSchemaService()
        self.runtime_loop_service = RuntimeLoopService()
        self.state_service = StateService(
            workspace_root_getter=lambda: self.workspace_root,
            state_file_getter=lambda: self.state_file,
            task_manager_getter=lambda: getattr(self, "task_manager", None),
        )
        self.context_service = ContextService(
            context_manager=self.context_manager,
            message_compactor=self.message_compactor,
            memory_flush_session=self.memory_flush_session,
            memory_flush_coordinator=self.memory_flush_coordinator,
            memory_manager=self.memory_manager,
            planning_service=self.planning_service,
            audit_logger=self.audit_logger,
            compact_memory_max_chars=self.COMPACT_MEMORY_MAX_CHARS,
        )
        
        # Register retrieve_memory tool
        self.register_tool(Tool(
            name="retrieve_memory",
            description="Retrieve relevant memories from long-term storage using semantic search and keywords.",
            handler=self._handle_retrieve_memory,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant memories."
                    }
                },
                "required": ["query"]
            },
            need_auth=False,
            type=ToolType.FILE  # Treat as file/memory operation
        ))
        
    def register_tool(self, tool: Tool) -> None:
        """Register a tool with the agent."""
        self.tools[tool.name] = tool
        
        # Also register with tool adaptation layer (Phase 2)
        # Default: auto-detect based on tool type
        self.tool_adaptation_layer.register_tool_metadata(tool)
        
        logger.info(f"Registered tool: {tool.name} (need_auth={tool.need_auth})")
        
    def set_middleware_chain(self, chain: MiddlewareChain) -> None:
        """Set the middleware chain for permission processing."""
        self.middleware_chain = chain
        self.tool_execution_service.update_middleware_chain(chain)
        
    async def start_session(self, 
                           session_id: str,
                           user_id: str,
                           channel_type: str) -> Session:
        """Start a new conversation session.
        
        Note: Does NOT wipe message_history if it was already populated
        (e.g. from load_state_from_disk). Only initializes to [] if empty.
        """
        session = Session(
            session_id=session_id,
            user_id=user_id,
            channel_type=channel_type,
        )
        self.current_session = session
        # Preserve history loaded from state.json; only init if truly empty
        if not self.message_history:
            self.message_history = []
            logger.info(f"[DEBUG] start_session: message_history was empty, initialized fresh")
        else:
            logger.info(f"[DEBUG] start_session: PRESERVING {len(self.message_history)} existing messages")
        self.state = AgentState.RUNNING
        logger.info(f"Started session {session_id} for user {user_id} (history={len(self.message_history)} msgs)")
        return session
        
    async def process_message(self, message: Message) -> dict[str, Any]:
        """Process a single user message through the Phase5 orchestrator path."""
        if not self.current_session:
            raise RuntimeError("No active session")
        
        # STATE LOCK: Clear auth if new message arrives
        if self.state == AgentState.WAITING_FOR_AUTH:
            logger.info(f"Clearing pending auth requests: user sent new message while WAITING_FOR_AUTH")
            self.pending_auth_requests.clear()
            self.state = AgentState.RUNNING

        return await self._process_message_v2_orchestrated(message)

    def _phase5_max_steps(self) -> int:
        """Resolve max orchestrator steps from config metadata."""
        phase5 = self.config.metadata.get("phase5", {})
        max_steps = phase5.get("max_steps", 6)
        try:
            return max(1, int(max_steps))
        except (TypeError, ValueError):
            return 6

    async def _process_message_v2_orchestrated(self, message: Message) -> dict[str, Any]:
        """Phase 5 P1 orchestrator flow (PLAN -> ACT -> OBSERVE -> DECIDE).

        MVP action space is intentionally constrained to:
        - tool_call
        - final_answer
        - plan_update
        """
        self.message_history.append(message)

        run_state = RunState(
            run_id=f"run_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            user_message=message,
            budget=RunBudget(max_steps=self._phase5_max_steps()),
            metadata={
                "stagnation_count": 0,
                "force_replan": False,
                "stop_after_replan": False,
                "todo_store": TodoStore(),
            },
        )

        def _phase5_build_messages_for_planner(state: RunState) -> list[dict[str, Any]]:
            # Start from persisted conversation and append transient in-run tool traces.
            # Without this, iterative planning can lose awareness of already executed tools.
            messages_for_llm = self._format_conversation_for_llm()

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

            messages_for_llm = _phase5_build_messages_for_planner(state)
            tools_for_llm = self._format_tools_for_llm()
            llm_response, tool_calls = await self.planning_service.generate_plan_or_answer(
                messages=messages_for_llm,
                tools=tools_for_llm,
                temperature=self.config.temperature,
            )

            if tool_calls:
                if len(tool_calls) > 1:
                    state.pending_actions.extend(tool_calls[1:])
                    logger.info(f"Phase5 P1.5 queued {len(tool_calls) - 1} additional tool action(s)")
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

        async def actor(state: RunState, action: Action) -> Observation:
            if action.type == "tool_call":
                tool_call: ToolCall = action.payload["tool_call"]
                tool_result = await self._process_tool_call(tool_call)
                return Observation(
                    kind="tool_result",
                    status=tool_result.status,
                    data={"tool_call": tool_call, "tool_result": tool_result},
                    error=tool_result.error,
                )

            if action.type == "final_answer":
                text = action.payload.get("text")
                if action.payload.get("mode") == "synthesize_from_observations":
                    text = await self._phase5_synthesize_answer(state)
                return Observation(
                    kind="final_answer",
                    status="success",
                    data={"text": text or self._phase5_fallback_summary(state)},
                )

            return Observation(
                kind="plan_update",
                status="success",
                data={
                    "text": action.payload.get("text") or json.dumps(action.payload, ensure_ascii=False),
                    "payload": action.payload,
                },
            )

        def observer(state: RunState, action: Action, observation: Observation) -> RunState:
            if observation.kind == "tool_result":
                state.tool_calls.append(observation.data["tool_call"])
                state.tool_results.append(observation.data["tool_result"])
            return state

        def decider(state: RunState, action: Action, observation: Observation) -> Decision:
            if observation.kind == "tool_result":
                tool_result: ToolResult = observation.data["tool_result"]
                if tool_result.status == "auth_required":
                    self.state = AgentState.WAITING_FOR_AUTH
                    pending_auth = tool_result.metadata
                    auth_request_id = pending_auth.get("auth_request_id") if pending_auth else None
                    if auth_request_id:
                        self._phase5_auth_paused_runs[auth_request_id] = state

                    assistant_message = state.metadata.get("initial_llm_response") or self._phase5_fallback_summary(state)
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
                        state.metadata["replan_payload"] = _build_structured_plan_update_payload(
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

                response_text = observation.data.get("text") or self._phase5_fallback_summary(state)
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

        output = await self.orchestrator_engine.run(
            run_state,
            planner,
            actor,
            observer,
            decider,
            guards=self.orchestrator_guards,
            hooks=self.orchestrator_hooks,
        )

        if self._critical_trim_notice_pending:
            warning_text = (
                "[CONTEXT WARNING] Context reached CRITICAL. "
                "History was hard-trimmed to the latest 10 rounds to recover stability."
            )
            current_response = output.get("response") or ""
            output["response"] = f"{warning_text}\n\n{current_response}" if current_response else warning_text
            self._critical_trim_notice_pending = False

        self.message_history.append(Message(
            id=f"msg_{datetime.now(timezone.utc).timestamp()}",
            channel_type=self.current_session.channel_type if self.current_session else "unknown",
            sender_id=self.agent_id,
            sender_name="Agent",
            content=output.get("response", "No response generated."),
            tool_calls=run_state.tool_calls or None,
            tool_results=run_state.tool_results or None,
        ))

        return output

    async def _phase5_pre_act_context_guard(self, state: RunState, action: Action) -> Optional[Any]:
        """P2-B: run-scoped context pressure hook invoked by PreActContextGuard."""
        if state.metadata.get("context_guard_checked", False):
            return None

        state.metadata["context_guard_checked"] = True
        prior_session_id = self.memory_flush_coordinator.last_flush_session_id
        await self._maybe_trigger_memory_flush_before_planning()

        new_session_id = self.memory_flush_coordinator.last_flush_session_id
        if new_session_id and new_session_id != prior_session_id:
            state.metadata["flush_triggered_in_run"] = True

        return None

    async def _maybe_trigger_memory_flush_before_planning(self) -> None:
        """Execute memory flush when context reaches WARNING threshold."""
        if self._memory_flush_in_progress:
            return

        messages_for_check = self._format_conversation_for_llm()
        session_id = await self.context_service.maybe_trigger_memory_flush_before_planning(
            messages_for_check=messages_for_check,
        )
        if not session_id:
            return

        await self._execute_memory_flush_standalone(session_id)

    async def _execute_memory_flush_standalone(self, session_id: str) -> None:
        """Run memory flush mini-loop and persist compact snapshot for next prompts."""
        self._memory_flush_in_progress = True
        try:
            updated_history, updated_snapshot = await self.context_service.execute_memory_flush_standalone(
                session_id=session_id,
                message_history=self.message_history,
                agent_id=self.agent_id,
                current_user_id=self.current_session.user_id if self.current_session else "system",
                process_tool_call=self._process_tool_call,
                format_tools_for_llm=self._format_tools_for_llm,
                format_conversation_for_llm=self._format_conversation_for_llm,
                current_compact_memory_snapshot=self.compact_memory_snapshot,
            )

            self.message_history = updated_history
            self.compact_memory_snapshot = updated_snapshot
        finally:
            self._memory_flush_in_progress = False

    async def _phase5_synthesize_answer(self, state: RunState) -> str:
        """Generate same-turn final answer from observed tool results.

        Uses full conversation context plus current-run tool traces to avoid
        short-prompt regressions in tool/auth-heavy turns.
        """
        synthesis_messages = self._format_conversation_for_llm()

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
            logger.error(f"Phase5 synthesis failed: {exc}")

        return self._phase5_fallback_summary(state)

    def _phase5_fallback_summary(self, state: RunState) -> str:
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

    async def _phase5_resume_after_auth(self,
                                        auth_request_id: str,
                                        auth_result: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Resume a paused Phase5 run after an auth decision and auto-finalize output."""
        state = self._phase5_auth_paused_runs.pop(auth_request_id, None)
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

        # Replace the auth_required placeholder result with a concrete success result.
        for tr in reversed(state.tool_results):
            if tr.status == "auth_required":
                tr.status = "success"
                tr.result = auth_result.get("result")
                tr.error = None
                break

        # Continue queued actions until complete or until another auth gate appears.
        while state.pending_actions:
            tool_call = state.pending_actions.pop(0)
            tool_result = await self._process_tool_call(tool_call)
            state.tool_calls.append(tool_call)
            state.tool_results.append(tool_result)

            if tool_result.status == "auth_required":
                pending_auth = tool_result.metadata
                next_auth_id = pending_auth.get("auth_request_id") if pending_auth else None
                if next_auth_id:
                    self._phase5_auth_paused_runs[next_auth_id] = state
                self.state = AgentState.WAITING_FOR_AUTH
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

        final_text = await self._phase5_synthesize_answer(state)
        return {
            "response": final_text,
            "tool_calls": [tc.to_dict() for tc in state.tool_calls],
            "tool_results": [tr.to_dict() for tr in state.tool_results],
            "requires_auth": False,
            "memory_flushed": False,
        }
    
    async def _process_tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call via unified ToolExecutionService entrypoint."""
        result = await self.tool_execution_service.execute_tool_call(tool_call)

        if result.status == "auth_required":
            auth_request_id = self.auth_service.remember(result.metadata)
            if auth_request_id:
                logger.info(f"Auth required for tool {tool_call.name}: {auth_request_id}")

        return result
    
    async def approve_auth_request(self,
                                  auth_request_id: str,
                                  user_id: str,
                                  approved: bool) -> dict[str, Any]:
        """User approves or rejects authorization for a sensitive operation.
        
        Returns: {
            "auth_request_id": str,
            "status": "approved" | "rejected",
            "result": any (if approved and re-executed)
        }
        """
        status, pending_auth, error = self.auth_service.consume(
            auth_request_id=auth_request_id,
            user_id=user_id,
            approved=approved,
        )

        if status == "rejected":
            self.state = AgentState.RUNNING
            return {
                "auth_request_id": auth_request_id,
                "status": "rejected",
            }

        if status == "error" or pending_auth is None:
            self.state = AgentState.RUNNING
            return {
                "auth_request_id": auth_request_id,
                "status": "error",
                "error": error or "Auth request not found",
            }

        pending_auth = {**pending_auth}
        pending_args = pending_auth.get("arguments")
        if isinstance(pending_args, dict):
            pending_args["_force_execute"] = True

        try:
            result = await self.tool_execution_service.execute_authorized_request(pending_auth)
            self.state = AgentState.RUNNING
            return {
                "auth_request_id": auth_request_id,
                "status": "approved",
                "result": result,
            }
        except Exception as e:
            logger.error(f"Tool execution error after approval: {e}")
            self.state = AgentState.RUNNING
            return {
                "auth_request_id": auth_request_id,
                "status": "error",
                "error": str(e),
            }
    
    def _format_conversation_for_llm(self) -> list[dict[str, Any]]:
        """Format conversation history for LLM input with context management.
        
        Phase 4 Enhancement:
        - Count tokens in the formatted conversation
        - Apply message compression if approaching token limit
        - Log context usage metrics
        - Display token usage information for user awareness
        """
        logger.info(f"[DEBUG] _format_conversation_for_llm: processing {len(self.message_history)} raw messages from history")
        messages = []
        
        # Add system prompt with token usage information
        system_content = self._build_system_prompt()
        
        # Build the enhanced system prompt with token usage display
        # This will be updated after we count tokens, so we'll add a placeholder
        system_message = {
            "role": "system",
            "content": system_content,
        }
        messages.append(system_message)

        compact_pair = self._build_compact_memory_pair()
        if compact_pair:
            messages.extend(compact_pair)
        
        self.context_service.append_formatted_history_messages(
            target_messages=messages,
            message_history=self.message_history,
            agent_id=self.agent_id,
        )
        
        # Phase 4: Token counting and context management
        context_eval = self.context_service.analyze_context_usage(messages)
        token_count = context_eval["token_count"]
        status = context_eval["status"]
        should_flush = context_eval["should_flush"]
        context_report = context_eval["context_report"]
        logger.info(f"[CONTEXT] Token usage: {context_report['usage_percentage']} ({token_count}/{self.context_manager.max_tokens}), Status: {status}")
        
        # Enhance system prompt with token usage information
        token_usage_info = context_eval["token_usage_info"]
        system_message["content"] = system_content + token_usage_info
        
        usage_ratio = context_eval["usage_ratio"]
        
        # If reaching CRITICAL threshold, perform deterministic hard fallback.
        if status == "CRITICAL":
            trim_result = self.context_service.apply_critical_trim_policy(
                message_history=self.message_history,
                capture_compact_memory_snapshot=self._capture_compact_memory_snapshot,
                keep_turns=10,
            )
            compact_snapshot = trim_result["compact_snapshot"]
            if compact_snapshot:
                self.compact_memory_snapshot = compact_snapshot
                logger.info("[COMPACT_MEMORY] Updated compact memory snapshot from pre-CRITICAL fallback context")

            old_size = trim_result["old_size"]
            keep_turns = trim_result["keep_turns"]
            self.message_history = trim_result["message_history"]

            logger.warning(
                f"[CRITICAL_CONTEXT_FALLBACK] status=CRITICAL, usage={usage_ratio:.3f}, "
                f"message_history={old_size}->{len(self.message_history)} (keep_last={keep_turns} rounds)."
            )
            self._critical_trim_notice_pending = True

            messages = [
                {
                    "role": "system",
                    "content": self._build_system_prompt(),
                }
            ]
            compact_pair = self._build_compact_memory_pair()
            if compact_pair:
                messages.extend(compact_pair)
            self.context_service.append_formatted_history_messages(
                target_messages=messages,
                message_history=self.message_history,
                agent_id=self.agent_id,
            )

            context_eval = self.context_service.analyze_context_usage(messages)
            token_count = context_eval["token_count"]
            status = context_eval["status"]
            should_flush = context_eval["should_flush"]
            context_report = context_eval["context_report"]
            token_usage_info = context_eval["token_usage_info"]
            messages[0]["content"] = self._build_system_prompt(token_usage_info)
            logger.warning(f"[CRITICAL_CONTEXT_FALLBACK] rebuilt prompt tokens={token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})")
        
        # Apply surgical repair to the transcript before returning
        repaired_messages = self._repair_transcript(messages)
        
        # Log final context report
        if status != "OK":
            self.context_service.log_context_threshold_warning(
                status=status,
                should_flush=should_flush,
                context_report=context_report,
                token_count=token_count,
                current_user_id=self.current_session.user_id if self.current_session else "system",
            )
        
        return repaired_messages

    def _extract_compact_memory_block(self, text: str) -> Optional[str]:
        """Extract structured compact memory block if present."""
        return self.context_service.extract_compact_memory_block(text)

    def _normalize_compact_memory(self, text: str) -> Optional[str]:
        """Normalize compact memory text and apply safety/length guards."""
        return self.context_service.normalize_compact_memory(text)

    def _capture_compact_memory_snapshot(self) -> Optional[str]:
        """Capture compact memory from latest assistant content before compaction."""
        return self.context_service.capture_compact_memory_snapshot(
            message_history=self.message_history,
            agent_id=self.agent_id,
        )

    def _build_compact_memory_pair(self) -> list[dict[str, Any]]:
        """Build synthetic user/assistant pair carrying compact memory snapshot."""
        return self.context_service.build_compact_memory_pair(self.compact_memory_snapshot)

    def _build_system_prompt(self, suffix: str = "") -> str:
        """Build system prompt with baseline behavior and optional recall guidance."""
        return self.context_service.build_system_prompt(
            base_prompt=self.config.system_prompt or "",
            has_retrieve_memory_tool="retrieve_memory" in self.tools,
            suffix=suffix,
        )

    def _build_memory_recall_block(self) -> str:
        """Return recall policy guidance if memory retrieval is available."""
        return self.context_service.build_memory_recall_block("retrieve_memory" in self.tools)
    
    def _repair_transcript(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Repair transcript through ContextService to keep core focused on orchestration."""
        return self.context_service.repair_transcript(
            messages=messages,
            current_user_id=self.current_session.user_id if self.current_session else "system",
        )

    def _format_tools_for_llm(self) -> list[dict[str, Any]]:
        """Format registered tools for LLM function calling."""
        return self.tool_schema_service.format_tools_for_llm(self.tools.values())

    async def _handle_retrieve_memory(self, query: str) -> str:
        """Handle retrieve_memory tool call."""
        return await self.context_service.retrieve_memory(
            query=query,
            session_id=self.current_session.session_id if self.current_session else None,
        )
    
    def pause(self) -> None:
        """Pause agent execution."""
        self.state = AgentState.PAUSED
        logger.info("Agent paused")
    
    def resume(self) -> None:
        """Resume agent execution."""
        self.state = AgentState.RUNNING
        logger.info("Agent resumed")
    
    async def end_session(self) -> None:
        """End current session."""
        if self.current_session:
            logger.info(f"Ended session {self.current_session.session_id}")
            self.current_session = None
        self.state = AgentState.IDLE
    
    # --- TaskManager Integration Interface (Phase 2) ---
    # These methods are placeholders for TaskManager integration in Phase 2
    
    def set_task_manager(self, task_manager: Any) -> None:
        """Set the background task manager.
        
        Args:
            task_manager: TaskManager instance for handling long-running operations
        
        Usage in Phase 2:
            agent.set_task_manager(task_manager)
        """
        self.task_manager = task_manager
        logger.info("TaskManager integrated with AgentCore")
    
    async def poll_background_tasks(self) -> list[dict[str, Any]]:
        """Poll for completed background tasks from TaskManager.
        
        Called from main loop to check if any background tasks have completed.
        
        Returns:
            List of completed task results, each containing:
            {
                "task_id": str,
                "status": "completed" | "failed" | "cancelled",
                "result": Any,
                "error": Optional[str]
            }
        
        Usage in Phase 2:
            completed = await agent.poll_background_tasks()
            for task in completed:
                notify_user(task["task_id"], task["result"])
        """
        if not hasattr(self, 'task_manager') or not self.task_manager:
            return []
        
        # Delegate to TaskManager (to be implemented in Phase 2)
        completed_tasks = await self.task_manager.poll_results()
        return completed_tasks
    
    async def create_background_task(self, 
                                    tool_name: str, 
                                    arguments: dict[str, Any]) -> str:
        """Create a background task for long-running tool execution.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
        
        Returns:
            task_id: Unique task identifier (e.g., "#001")
        
        Usage in Phase 2:
            task_id = await agent.create_background_task("web_search", {...})
            response = f"Task {task_id} started in background"
        """
        if not hasattr(self, 'task_manager') or not self.task_manager:
            raise RuntimeError("TaskManager not configured")
        
        # Delegate to TaskManager (to be implemented in Phase 2)
        task_id = await self.task_manager.create_task(tool_name, arguments)
        logger.info(f"Created background task: {task_id}")
        return task_id
    
    # --- Main Agent Loop (Phase 2) ---
    
    async def run(self, 
                 session_id: str,
                 user_id: str,
                 channel_type: str,
                 message_input_fn,  # Async callable: () -> Message
                 message_output_fn,  # Async callable: (response_dict) -> None
                 auth_response_fn=None,  # Async callable: (auth_request_id, timeout) -> AuthorizationResponse|None
                 state: Optional[dict[str, Any]] = None) -> None:
        """Main synchronous agent loop with async background task support.
        
        Implements the core loop as defined in Planning.md Section "Synchronous main loop + async TaskManager":
        - Synchronous main loop (easy to debug)
        - Long-running ops via TaskManager (asyncio.create_task) non-blocking
        - HITL confirmation for need_auth tools (immediate confirmation)
        - Full state persistence (complete persistence)

        Args:
            session_id: Conversation session ID
            user_id: User identifier for auth checks
            channel_type: Communication channel (telegram/feishu/cli)
            message_input_fn: Async callable to receive user messages -> Message
            message_output_fn: Async callable to send response -> dict
            state: Optional state.json data to restore from (for agent restart)
        
                Loop Flow (Planning.md):
                        User input -> Agent sync handling -> detect long-running tool call
                            ->
                        Tool is not run directly -> route to TaskManager -> asyncio.create_task()
                            ->
                        Tool immediately returns task_id (e.g. "#001") -> Agent continues loop
                            ->
                        Main loop calls poll_results() each cycle -> check completed background tasks
                            ->
                        Task completes -> Agent proactively pushes result to user
        
        Features:
            - Synchronous main loop (easy to debug, avoids event stream complexity)
            - Background tasks via TaskManager (asyncio.create_task, non-blocking)
            - HITL confirmation for need_auth tools (WAITING_FOR_AUTH state)
            - State persistence (state.json with active_tasks, message history)
            - Task resume on agent restart (load_from_state)
        """
        logger.info(f"Agent.run() starting: session={session_id}, user={user_id}, channel={channel_type}")
        
        # Restore state if provided (agent restart scenario)
        if state:
            await self._restore_state(state)
            logger.info("Restored agent state from persistence")
        
        try:
            # Start session
            await self.start_session(session_id, user_id, channel_type)
            
            # Main synchronous loop
            while self.state in (AgentState.RUNNING, AgentState.WAITING_FOR_AUTH):
                try:
                    # ========== 1. Poll Completed Background Tasks ==========
                    # Check if any background tasks have completed
                    # This is NON-BLOCKING - poll_results() returns immediately
                    completed_tasks = await self.poll_background_tasks()
                    for task_result in completed_tasks:
                        await self.runtime_loop_service.emit_task_completed(message_output_fn, task_result)
                        logger.info(f"Notified user of completed task: {task_result.get('task_id')}")
                    
                    # ========== 2. Get User Input (Blocking Wait) ==========
                    # In CLI mode: blocks for input
                    # In channel mode: gets next message from queue
                    user_message = await message_input_fn()
                    if not user_message:
                        # Input source closed (e.g., channel disconnect)
                        logger.info("Message input source closed, ending session")
                        break
                    
                    logger.info(f"Processing user message from {user_message.sender_id}")
                    
                    # ========== 3. Process User Message ==========
                    # Calls LLM, executes tool calls, checks permissions
                    # Returns immediately (doesn't block on long operations)
                    response = await self.process_message(user_message)
                    
                    # ========== 4. Handle Auth Request (if any) ==========
                    # Sensitive operations return with "requires_auth": True
                    if response.get("requires_auth"):
                        auth_request_id = response.get("auth_request_id")
                        pending_auth = response.get("pending_auth", {})

                        assistant_text = response.get("assistant_message") or response.get("response")
                        if assistant_text:
                            await self.runtime_loop_service.emit_assistant_message(
                                message_output_fn,
                                response=assistant_text,
                                tool_calls=response.get("tool_calls", []),
                                tool_results=response.get("tool_results", []),
                            )
                        
                        # Send to user with auth buttons
                        await self.runtime_loop_service.emit_auth_request(
                            message_output_fn,
                            auth_request_id=auth_request_id,
                            tool_name=pending_auth.get("tool_name"),
                            description=pending_auth.get("description"),
                            diff_preview=pending_auth.get("diff_preview"),
                        )
                        logger.info(f"Auth request sent to user: {auth_request_id}")
                        
                        # ========== 4b. Await Auth Response OR New Message ==========
                        # We must listen to BOTH auth responses and new chat messages.
                        # If a new message arrives, we cancel the auth request.
                        if auth_response_fn:
                            logger.info(f"[DEBUG] Awaiting auth_response_fn for {auth_request_id}...")

                            wait_result = await self.runtime_loop_service.await_auth_or_message(
                                auth_response_fn=auth_response_fn,
                                message_input_fn=message_input_fn,
                                auth_request_id=auth_request_id,
                                timeout_seconds=300.0,
                            )

                            if wait_result.get("kind") == "new_message":
                                # User sent a new message! Cancel auth.
                                logger.info("User sent new message during auth wait. Cancelling auth.")
                                self.pending_auth_requests.pop(auth_request_id, None)
                                self.state = AgentState.RUNNING
                                # Process the new message immediately
                                new_msg = wait_result.get("message")
                                if new_msg:
                                    logger.info(f"Processing interrupting message: {new_msg.content[:50]}")
                                await self.runtime_loop_service.handle_auth_interruption(
                                    message_output_fn,
                                    message_history=self.message_history,
                                    channel_type=channel_type,
                                    interrupt_message=new_msg,
                                    process_message_fn=self.process_message,
                                )
                                continue
                                
                            else:
                                # Auth task finished!
                                auth_resp = wait_result.get("auth_response")
                                if auth_resp:
                                    approved = auth_resp.approved
                                    auth_user = auth_resp.user_id
                                    logger.info(f"Auth response: {'approved' if approved else 'rejected'} by {auth_user}")
                                    
                                    auth_result = await self.approve_auth_request(
                                        auth_request_id=auth_request_id,
                                        user_id=auth_user,
                                        approved=approved,
                                    )

                                    resume_payload = await self._phase5_resume_after_auth(auth_request_id, auth_result)

                                    await self.runtime_loop_service.emit_auth_response_resolution(
                                        message_output_fn,
                                        message_history=self.message_history,
                                        channel_type=channel_type,
                                        approved=approved,
                                        auth_result=auth_result,
                                        resume_payload=resume_payload,
                                    )
                                else:
                                    # Timed out or no response
                                    logger.warning(f"Auth request {auth_request_id} timed out")
                                    self.pending_auth_requests.pop(auth_request_id, None)
                                    self.state = AgentState.RUNNING
                                    await self.runtime_loop_service.emit_auth_timeout_resolution(
                                        message_output_fn,
                                        message_history=self.message_history,
                                        channel_type=channel_type,
                                    )
                        # If no auth_response_fn, the old behavior applies:
                        # agent stays in WAITING_FOR_AUTH and user can send a new message
                    else:
                        # Normal response, send to user
                        await self.runtime_loop_service.emit_response(
                            message_output_fn,
                            response=response.get("response"),
                            tool_calls=response.get("tool_calls", []),
                            tool_results=response.get("tool_results", []),
                        )
                        logger.info("Response sent to user")
                    
                    # ========== 5. Persist State ==========
                    # Save to state.json after each message
                    # Ensures task recovery on crash/restart
                    state_snapshot = await self._save_state()
                    logger.debug(f"State persisted: {len(state_snapshot.get('active_tasks', {}))} active tasks")
                    
                except Exception as loop_error:
                    logger.error(f"Error in main loop: {loop_error}", exc_info=True)
                    await self.runtime_loop_service.emit_error(
                        message_output_fn,
                        error=f"Loop error: {str(loop_error)}",
                    )
                    # Continue loop instead of breaking
        
        except Exception as e:
            logger.error(f"Fatal error in Agent.run(): {e}", exc_info=True)
            await self.runtime_loop_service.emit_error(
                message_output_fn,
                error=f"Fatal: {str(e)}",
            )
        
        finally:
            # Cleanup
            await self.end_session()
            logger.info(f"Agent.run() ended for session={session_id}")
    
    async def _save_state(self) -> dict[str, Any]:
        """Save agent state to dict and persist to state.json.
        
        Structure for state.json:
        {
            "version": "0.1",
            "agent_state": "running",
            "last_save_time": "2026-03-15T10:30:00Z",
            "active_tasks": {...},           # From TaskManager
            "message_history": [...],        # Conversation history
            "pending_auth_requests": {...},  # Outstanding auth requests
        }
        """
        state_dict = await self.state_service.build_state_snapshot(
            agent_state=self.state.value,
            message_history=self.message_history,
            compact_memory_snapshot=self.compact_memory_snapshot,
            pending_auth_requests=self.pending_auth_requests,
        )
        await self.state_service.persist_state_snapshot(
            state_dict=state_dict,
            message_count=len(self.message_history),
        )
        return state_dict
    
    async def load_state_from_disk(self) -> None:
        """Attempt to restore state from state.json on disk."""
        state_dict = await self.state_service.load_state_dict_from_disk()
        if not state_dict:
            return

        await self._restore_state(state_dict)
        logger.info("[DEBUG] load_state_from_disk: after _restore_state, self.message_history has %s entries", len(self.message_history))
        logger.info("Loaded persisted agent state from disk")
    
    async def _restore_state(self, state_dict: dict[str, Any]) -> None:
        """Restore agent state from dictionary (state.json).
        
        Restores:
        - Message history
        - TaskManager tasks (active + completed)
        - Pending auth requests
        """
        history = self.state_service.deserialize_message_history(state_dict)
        if history is not None:
            self.message_history = history

        self.compact_memory_snapshot = self.state_service.restore_compact_memory_snapshot(state_dict)
        await self.state_service.restore_task_manager_state(state_dict)
        
        self.pending_auth_requests = self.state_service.restore_pending_auth_requests(state_dict)
        logger.info(f"Restored {len(self.pending_auth_requests)} pending auth requests")


