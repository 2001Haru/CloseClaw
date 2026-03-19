"""Agent core implementation."""

import asyncio
import logging
import os
import json
from typing import Any, Optional, Protocol
from datetime import datetime

from ..types import (
    Agent, AgentConfig, Session, Tool, Message,
    AgentState, Zone, ToolCall, ToolResult, ToolType
)
from ..middleware import MiddlewareChain
from ..tools.adaptation import ToolAdaptationLayer
from ..safety import AuditLogger
from ..context import ContextManager, MessageCompactor
from ..memory import MemoryFlushCoordinator, MemoryFlushSession, MemoryManager
from ..orchestrator import Action, Decision, Observation, OrchestratorEngine, PlanPolicy, RunBudget, RunState

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
    - Requests HITL confirmation for Zone C operations
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
        
        # Phase 3.5: Transcript Repair防火墙 - 审计日志初始化
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
            summarize_window=config.context_management.summarize_window,
            active_window=config.context_management.active_window,
            chunk_size=config.context_management.chunk_size
        )

        # Phase 4 Step 2: Memory flush manager (WARNING threshold trigger)
        self.memory_flush_session = MemoryFlushSession(workspace_root=workspace_root)
        self.memory_flush_coordinator = MemoryFlushCoordinator(self.memory_flush_session)
        
        # Phase 4 Step 3: Memory Manager - SQLite + Vector Search
        self.memory_manager = MemoryManager(workspace_root=workspace_root)

        # Phase 5: Single-loop orchestrator (MVP)
        self.orchestrator_engine = OrchestratorEngine()
        self.plan_policy = PlanPolicy()
        self._phase5_auth_paused_runs: dict[str, RunState] = {}
        
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
            zone=Zone.ZONE_A,  # Safe read-only tool
            type=ToolType.FILE  # Treat as file/memory operation
        ))
        
    def register_tool(self, tool: Tool) -> None:
        """Register a tool with the agent."""
        self.tools[tool.name] = tool
        
        # Also register with tool adaptation layer (Phase 2)
        # Default: auto-detect based on tool type
        self.tool_adaptation_layer.register_tool_metadata(tool)
        
        logger.info(f"Registered tool: {tool.name} (zone={tool.zone.value})")
        
    def set_middleware_chain(self, chain: MiddlewareChain) -> None:
        """Set the middleware chain for permission processing."""
        self.middleware_chain = chain
        
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

        # Trigger Phase4 memory flush before planning when context is at WARNING threshold.
        await self._maybe_trigger_memory_flush_before_planning()

        run_state = RunState(
            run_id=f"run_{int(datetime.utcnow().timestamp() * 1000)}",
            user_message=message,
            budget=RunBudget(max_steps=self._phase5_max_steps()),
        )

        def _phase5_serialize_tool_result(tool_result: ToolResult) -> str:
            if tool_result.status == "success":
                return tool_result.result if isinstance(tool_result.result, str) else json.dumps(tool_result.result)
            if tool_result.status == "auth_required":
                return "Operation requires user authorization. Waiting for approval."
            return f"Error or Blocked ({tool_result.status}): {tool_result.error}"

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
                    "content": _phase5_serialize_tool_result(tr),
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
            llm_response, tool_calls = await self.llm_provider.generate(
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
                data={"text": action.payload.get("text", "Plan updated.")},
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

                if state.pending_actions:
                    return Decision(stop=False, reason="continue_pending_actions")

                if tool_result.status in {"error", "blocked", "task_created"}:
                    return Decision(stop=False, reason="tool_finished_with_non_success")

                return Decision(stop=False, reason="tool_success_continue")

            if observation.kind in {"final_answer", "plan_update"}:
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

        output = await self.orchestrator_engine.run(run_state, planner, actor, observer, decider)

        if self._critical_trim_notice_pending:
            warning_text = (
                "[CONTEXT WARNING] Context reached CRITICAL. "
                "History was hard-trimmed to the latest 10 rounds to recover stability."
            )
            current_response = output.get("response") or ""
            output["response"] = f"{warning_text}\n\n{current_response}" if current_response else warning_text
            self._critical_trim_notice_pending = False

        self.message_history.append(Message(
            id=f"msg_{datetime.utcnow().timestamp()}",
            channel_type=self.current_session.channel_type if self.current_session else "unknown",
            sender_id=self.agent_id,
            sender_name="Agent",
            content=output.get("response", "No response generated."),
            tool_calls=run_state.tool_calls or None,
            tool_results=run_state.tool_results or None,
        ))

        return output

    async def _maybe_trigger_memory_flush_before_planning(self) -> None:
        """Execute memory flush when context reaches WARNING threshold."""
        if self._memory_flush_in_progress:
            return

        messages_for_check = self._format_conversation_for_llm()
        token_count = self.context_manager.count_message_tokens(messages_for_check)
        status, _ = self.context_manager.check_thresholds(token_count)
        usage_ratio = self.context_manager.get_usage_ratio(token_count)

        if not self.memory_flush_session.should_trigger_flush(status, usage_ratio):
            return

        session_id = self.memory_flush_coordinator.generate_session_id()
        self.memory_flush_coordinator.pending_flush = True
        self.memory_flush_coordinator.last_flush_session_id = session_id

        logger.warning(
            f"[MEMORY_FLUSH] Triggered before planning: session_id={session_id}, "
            f"usage={usage_ratio * 100:.1f}%"
        )

        await self._execute_memory_flush_standalone(session_id)

    async def _execute_memory_flush_standalone(self, session_id: str) -> None:
        """Run memory flush mini-loop and persist compact snapshot for next prompts."""
        self._memory_flush_in_progress = True
        try:
            temp_messages: list[dict[str, Any]] = [{
                "role": "system",
                "content": "You are an AI assistant preserving important conversation memory for future reference.",
            }]

            for msg in self.message_history:
                role = "user" if msg.sender_id != self.agent_id else "assistant"
                temp_messages.append({
                    "role": role,
                    "content": msg.content,
                })

            temp_messages.append({
                "role": "user",
                "content": self.memory_flush_session.create_flush_system_prompt(),
            })

            tools_for_llm = self._format_tools_for_llm()
            max_loops = 5

            for _ in range(max_loops):
                llm_response, tool_calls = await self.llm_provider.generate(
                    messages=temp_messages,
                    tools=tools_for_llm,
                    temperature=0.3,
                )

                compact_block = self._extract_compact_memory_block(llm_response or "")
                if compact_block:
                    normalized = self._normalize_compact_memory(compact_block)
                    if normalized:
                        self.compact_memory_snapshot = normalized

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": llm_response or "",
                }
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": tc.tool_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments,
                            },
                        }
                        for tc in tool_calls
                    ]
                temp_messages.append(assistant_message)

                if self.memory_flush_session.check_for_silent_reply(llm_response):
                    break

                for tc in tool_calls or []:
                    result = await self._process_tool_call(tc)
                    tool_content = (
                        json.dumps(result.result) if result.status == "success" and not isinstance(result.result, str)
                        else (result.result if result.status == "success" else f"Error: {result.error}")
                    )
                    temp_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.tool_id,
                        "content": tool_content or "",
                    })

            saved_files = self.memory_flush_session.collect_saved_memories()
            self.memory_flush_session.record_flush_event(
                user_id=self.current_session.user_id if self.current_session else "system",
                session_id=session_id,
                saved_files=saved_files,
                context_ratio=self.context_manager.get_usage_ratio(
                    self.context_manager.count_message_tokens(self._format_conversation_for_llm())
                ),
                audit_logger=self.audit_logger,
            )

            # Keep recent interaction window; compact snapshot preserves prior context.
            keep = max(self.message_compactor.active_window * 2, 5)
            if len(self.message_history) > keep:
                self.message_history = self.message_history[-keep:]

            self.memory_flush_coordinator.clear_pending_flush()
            logger.warning(
                f"[MEMORY_FLUSH] Completed: session_id={session_id}, files_saved={len(saved_files)}"
            )
        except Exception as exc:
            logger.error(f"[MEMORY_FLUSH] Failed: {exc}")
            self.memory_flush_coordinator.clear_pending_flush()
        finally:
            self._memory_flush_in_progress = False

    async def _phase5_synthesize_answer(self, state: RunState) -> str:
        """Generate same-turn final answer from observed tool results.

        Uses full conversation context plus current-run tool traces to avoid
        short-prompt regressions in tool/auth-heavy turns.
        """
        synthesis_messages = self._format_conversation_for_llm()

        for tc, tr in zip(state.tool_calls, state.tool_results):
            if tr.status == "success":
                content = tr.result if isinstance(tr.result, str) else json.dumps(tr.result)
            elif tr.status == "auth_required":
                content = "Operation requires user authorization. Waiting for approval."
            else:
                content = f"Error or Blocked ({tr.status}): {tr.error}"

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
            response_text, _ = await self.llm_provider.generate(
                messages=synthesis_messages,
                tools=[],
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
        """Execute a tool call with middleware and permission checks.
        
        Phase 2 Integration: Uses ToolAdaptationLayer to decide between:
        - Direct execution (fast tools, < 2s)
        - Background execution via TaskManager (slow tools, > 2s)
        
        Returns a ToolResult with status:
        - "success": Tool executed successfully
        - "task_created": Background task created (task_id in result)
        - "error": Tool execution failed
        - "auth_required": Waiting for user authorization
        - "blocked": Blocked by safety filter
        """
        tool = self.tools.get(tool_call.name)
        if not tool:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=f"Tool '{tool_call.name}' not found",
            )
        
        # Pre-flight check via middleware
        if self.middleware_chain:
            auth_result = await self.middleware_chain.check_permission(
                tool=tool,
                arguments=tool_call.arguments,
                session=self.current_session,
                user_id=self.current_session.user_id,
            )
            
            if auth_result["status"] == "block":
                return ToolResult(
                    tool_call_id=tool_call.tool_id,
                    status="blocked",
                    result=None,
                    error=auth_result.get("reason", "Blocked by safety filter"),
                )
            
            if auth_result["status"] == "requires_auth":
                # PERSISTENCE: Store the auth request for later approval/rejection
                auth_request_id = auth_result.get("auth_request_id")
                self.pending_auth_requests[auth_request_id] = auth_result
                
                logger.info(f"Auth required for tool {tool_call.name}: {auth_request_id}")
                
                # Return auth_required with metadata
                result = ToolResult(
                    tool_call_id=tool_call.tool_id,
                    status="auth_required",
                    result=None,
                )
                result.metadata = auth_result
                return result
        
        # Phase 2: Route through adaptation layer (sync vs background)
        # This decides based on tool type and estimated duration
        result = await self.tool_adaptation_layer.execute_tool_call(
            tool_call=tool_call,
            available_tools=self.tools,
            task_manager=getattr(self, 'task_manager', None),
            direct_executor=self._execute_tool_directly,
        )
        
        return result
    
    async def _execute_tool_directly(self, tool_call: ToolCall) -> ToolResult:
        """Direct tool execution (for sync/fast tools).
        
        Used by ToolAdaptationLayer when a tool should be executed
        immediately (not routed to TaskManager).
        """
        tool = self.tools.get(tool_call.name)
        if not tool:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=f"Tool '{tool_call.name}' not found",
            )
        
        try:
            logger.info(f"Direct execution: {tool_call.name}")
            result = await tool.handler(**tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="success",
                result=result,
            )
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=str(e),
            )
    
    async def approve_auth_request(self,
                                  auth_request_id: str,
                                  user_id: str,
                                  approved: bool) -> dict[str, Any]:
        """User approves or rejects authorization for Zone C operation.
        
        Returns: {
            "auth_request_id": str,
            "status": "approved" | "rejected",
            "result": any (if approved and re-executed)
        }
        """
        # NOTE: The channel already verified that the user is an admin 
        # (e.g. TelegramChannel checks admin_user_ids before resolving the future).
        # So we trust the channel-level check here. Only warn if admin_user_id is
        # set and doesn't match, but still proceed since the channel already validated.
        if self.admin_user_id and user_id != self.admin_user_id:
            logger.info(f"Auth approval from channel-verified user {user_id} (core admin_user_id={self.admin_user_id})")
        
        if not approved:
            self.state = AgentState.RUNNING
            return {
                "auth_request_id": auth_request_id,
                "status": "rejected",
            }
        
        # Re-execute pending tool
        pending_auth = self.pending_auth_requests.pop(auth_request_id, None)
        if not pending_auth:
            return {
                "auth_request_id": auth_request_id,
                "status": "error",
                "error": "Auth request not found",
            }
        
        # Execute tool with force_execute flag
        tool_name = pending_auth.get("tool_name")
        arguments = pending_auth.get("arguments", {})
        arguments["_force_execute"] = True  # Override auth checks
        
        tool = self.tools.get(tool_name)
        if not tool:
            return {
                "auth_request_id": auth_request_id,
                "status": "error",
                "error": f"Tool '{tool_name}' not found",
            }
        
        # The permissions middleware needs _force_execute in the arguments
        # for logging and bypassing, but the actual tool handler signature
        # doesn't accept it. We extract it before calling the handler.
        _force = arguments.pop("_force_execute", False)
        
        try:
            result = await tool.handler(**arguments)
            # Restore it in case arguments dict is reused
            if _force:
                arguments["_force_execute"] = True
            
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
        import json
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
        
        def _append_history(target_messages: list[dict[str, Any]]) -> None:
            for msg in self.message_history:
                role = "user" if msg.sender_id != self.agent_id else "assistant"

                msg_dict: dict[str, Any] = {
                    "role": role,
                    "content": msg.content,
                }

                if role == "assistant" and msg.tool_calls:
                    # Format tool calls for OpenAI API
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.tool_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments)
                            }
                        } for tc in msg.tool_calls
                    ]

                target_messages.append(msg_dict)

                # If there are tool results, they must be appended as "tool" role messages
                if msg.tool_results:
                    for tr in msg.tool_results:
                        # Content must be a string. Default to JSON dump.
                        content = ""
                        if tr.status == "success":
                            content = json.dumps(tr.result) if not isinstance(tr.result, str) else tr.result
                        elif tr.status == "auth_required":
                            content = "Operation requires user authorization. Waiting for approval."
                        else:
                            content = f"Error or Blocked ({tr.status}): {tr.error}"

                        # Hard limit to prevent token overflows
                        MAX_RESULT_CHARS = 10000
                        if len(content) > MAX_RESULT_CHARS:
                            content = content[:MAX_RESULT_CHARS] + f"\n\n... [Output truncated because it exceeded {MAX_RESULT_CHARS} characters]"

                        target_messages.append({
                            "role": "tool",
                            "tool_call_id": tr.tool_call_id,
                            "content": content
                        })

        _append_history(messages)
        
        # Phase 4: Token counting and context management
        token_count = self.context_manager.count_message_tokens(messages)
        status, should_flush = self.context_manager.check_thresholds(token_count)
        
        context_report = self.context_manager.get_status_report(token_count)
        logger.info(f"[CONTEXT] Token usage: {context_report['usage_percentage']} ({token_count}/{self.context_manager.max_tokens}), Status: {status}")
        
        # Enhance system prompt with token usage information
        token_usage_info = f"\n\n[CONTEXT MONITOR] Current token usage: {token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})"
        system_message["content"] = system_content + token_usage_info
        
        usage_ratio = self.context_manager.get_usage_ratio(token_count)
        
        # If reaching CRITICAL threshold, perform deterministic hard fallback.
        if status == "CRITICAL":
            compact_snapshot = self._capture_compact_memory_snapshot()
            if compact_snapshot:
                self.compact_memory_snapshot = compact_snapshot
                logger.info("[COMPACT_MEMORY] Updated compact memory snapshot from pre-CRITICAL fallback context")

            keep_turns = 10
            keep_messages = keep_turns * 2
            old_size = len(self.message_history)
            if old_size > keep_messages:
                self.message_history = self.message_history[-keep_messages:]

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
            _append_history(messages)

            token_count = self.context_manager.count_message_tokens(messages)
            status, should_flush = self.context_manager.check_thresholds(token_count)
            context_report = self.context_manager.get_status_report(token_count)
            token_usage_info = f"\n\n[CONTEXT MONITOR] Current token usage: {token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})"
            messages[0]["content"] = self._build_system_prompt(token_usage_info)
            logger.warning(f"[CRITICAL_CONTEXT_FALLBACK] rebuilt prompt tokens={token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})")
        
        # Apply surgical repair to the transcript before returning
        repaired_messages = self._repair_transcript(messages)
        
        # Log final context report
        if status != "OK":
            logger.warning(f"[CONTEXT_WARNING] Status={status}, should_flush={should_flush}")
            try:
                self.audit_logger.log(
                    event_type="context_threshold_warning",
                    status=status,
                    user_id=self.current_session.user_id if self.current_session else "system",
                    tool_name="[system.context_manager]",
                    arguments=context_report,
                    result=f"Token count {token_count} exceeded {status.lower()} threshold"
                )
            except Exception as e:
                logger.error(f"Failed to log context warning: {e}")
        
        return repaired_messages

    def _extract_compact_memory_block(self, text: str) -> Optional[str]:
        """Extract structured compact memory block if present."""
        if not text:
            return None

        start = "[COMPACT_MEMORY_BLOCK]"
        end = "[/COMPACT_MEMORY_BLOCK]"
        s_idx = text.find(start)
        e_idx = text.find(end)
        if s_idx == -1 or e_idx == -1 or e_idx <= s_idx:
            return None

        block = text[s_idx + len(start):e_idx].strip()
        return block or None

    def _normalize_compact_memory(self, text: str) -> Optional[str]:
        """Normalize compact memory text and apply safety/length guards."""
        if not text:
            return None

        normalized_lines = []
        blank_streak = 0
        for raw_line in text.replace("\r\n", "\n").split("\n"):
            line = raw_line.strip()
            if not line:
                blank_streak += 1
                if blank_streak > 1:
                    continue
                normalized_lines.append("")
                continue
            blank_streak = 0
            normalized_lines.append(line)

        normalized = "\n".join(normalized_lines).strip()
        if not normalized:
            return None

        guard = (
            "This is a compressed memory summary and may be incomplete. "
            "If critical details are uncertain, verify via tools."
        )
        payload = f"{guard}\n\n{normalized}"

        if len(payload) > self.COMPACT_MEMORY_MAX_CHARS:
            payload = payload[: self.COMPACT_MEMORY_MAX_CHARS] + "..."

        return payload

    def _capture_compact_memory_snapshot(self) -> Optional[str]:
        """Capture compact memory from latest assistant content before compaction."""
        for msg in reversed(self.message_history):
            if msg.sender_id != self.agent_id:
                continue

            content = (msg.content or "").strip()
            if not content:
                continue

            extracted = self._extract_compact_memory_block(content)
            candidate = extracted or content
            normalized = self._normalize_compact_memory(candidate)
            if normalized:
                return normalized

        return None

    def _build_compact_memory_pair(self) -> list[dict[str, Any]]:
        """Build synthetic user/assistant pair carrying compact memory snapshot."""
        if not self.compact_memory_snapshot:
            return []

        return [
            {
                "role": "user",
                "content": (
                    "compact memory: previous context was compressed. "
                    "Please use the following summary as prior context before responding."
                ),
            },
            {
                "role": "assistant",
                "content": self.compact_memory_snapshot,
            },
        ]

    def _build_system_prompt(self, suffix: str = "") -> str:
        """Build system prompt with baseline behavior and optional recall guidance."""
        base_prompt = self.config.system_prompt or ""
        memory_recall_block = self._build_memory_recall_block()

        prompt_parts = [base_prompt.strip()] if base_prompt else []
        if memory_recall_block:
            prompt_parts.append(memory_recall_block)
        if suffix:
            prompt_parts.append(suffix.strip())

        return "\n\n".join(part for part in prompt_parts if part)

    def _build_memory_recall_block(self) -> str:
        """Return recall policy guidance if memory retrieval is available."""
        if "retrieve_memory" not in self.tools:
            return ""

        return """[MEMORY RECALL POLICY]
Before answering questions that depend on earlier decisions, preferences, constraints, TODOs, or historical commitments, call retrieve_memory first.

When to recall first:
- The user asks what was decided before.
- The user asks to continue prior tasks or plans.
- The user asks about preferences, environment constraints, or remembered facts.

How to respond:
- Ground the answer in retrieved memory results when available.
- If memory is missing or uncertain, say so clearly and ask a clarifying follow-up.
- Do not fabricate prior decisions or commitments."""
    
    def _repair_transcript(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Repair LLM transcript to satisfy strict API constraints.
        Ensures every tool_call in an assistant message has a corresponding tool result message,
        and no tool results exist without a parent tool call.
        
        Phase 3.5 Enhancement: Collect repair statistics and log to audit.log
        """
        repaired = []
        pending_tool_calls = {}  # tool_call_id -> function name
        
        # Phase 3.5: 修复统计
        stats = {
            "orphan_calls_removed": 0,
            "orphan_results_dropped": 0,
            "synthetic_results_added": 0,
            "calls_seen": [],  # 记录所有 tool_call_id
        }
        
        for msg in messages:
            role = msg.get("role")
            
            # If we transition to a non-tool message, check if we have unresolved tool calls
            if role in ["user", "assistant", "system"] and pending_tool_calls:
                # We have orphaned tool calls! Inject synthetic errors.
                for tc_id, func_name in pending_tool_calls.items():
                    repaired.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": "[System Repair] Tool execution interrupted or cancelled before completion. Synthetic error injected to repair transcript."
                    })
                    stats["synthetic_results_added"] += 1
                    stats["orphan_calls_removed"] += 1
                pending_tool_calls.clear()
            
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tc_id = tc["id"]
                    pending_tool_calls[tc_id] = tc["function"]["name"]
                    stats["calls_seen"].append(tc_id)
            
            if role == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id in pending_tool_calls:
                    del pending_tool_calls[tc_id]
                else:
                    # Orphan tool result without a parent. Drop it to prevent strict API crashes.
                    stats["orphan_results_dropped"] += 1
                    continue
            
            repaired.append(msg)
            
        # At the end of the transcript, tie up any remaining loose ends
        if pending_tool_calls:
            for tc_id, func_name in pending_tool_calls.items():
                repaired.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "[System Repair] Tool execution interrupted before completion. Synthetic error injected."
                })
                stats["synthetic_results_added"] += 1
                stats["orphan_calls_removed"] += 1
            pending_tool_calls.clear()
        
        # Phase 3.5: 记录修复统计至审计日志
        if stats["orphan_calls_removed"] > 0 or stats["orphan_results_dropped"] > 0:
            logger.info(
                f"[TRANSCRIPT_REPAIR] orphan_calls_removed={stats['orphan_calls_removed']} "
                f"orphan_results_dropped={stats['orphan_results_dropped']} "
                f"synthetic_results_added={stats['synthetic_results_added']}"
            )
            # 记录到审计日志
            try:
                self.audit_logger.log(
                    event_type="transcript_repair",
                    status="success",
                    user_id=self.current_session.user_id if self.current_session else "system",
                    tool_name="[system.transcript_repair]",
                    arguments={
                        "orphan_calls_removed": stats["orphan_calls_removed"],
                        "orphan_results_dropped": stats["orphan_results_dropped"],
                        "synthetic_results_added": stats["synthetic_results_added"],
                    },
                    result=f"Repaired transcript: {len(repaired)} messages"
                )
            except Exception as e:
                logger.error(f"Failed to log transcript repair: {e}")
            
        return repaired

    def _format_tools_for_llm(self) -> list[dict[str, Any]]:
        """Format registered tools for LLM function calling.
        
        Outputs OpenAI-standard format.
        Robustly handles multiple parameter schema shapes:
        - Legacy: {"param": {"type": "...", "description": "..."}}
        - JSON Schema: {"type": "object", "properties": {...}, "required": [...]}
        - Shorthand: {"param": "string"}
        """
        tools_list: list[dict[str, Any]] = []
        for tool in self.tools.values():
            properties_schema: dict[str, Any] = {}
            required: list[str] = []
            
            raw_params = tool.parameters or {}
            
            # If tool.parameters is a JSON Schema with "properties", prefer that
            if isinstance(raw_params, dict) and "properties" in raw_params and isinstance(raw_params["properties"], dict):
                props_source = raw_params["properties"]
                required = list(raw_params.get("required", []))
            else:
                props_source = raw_params  # assume mapping param_name -> param_info
            
            # Build properties schema, tolerant to string or dict param_info
            for param_name, param_info in props_source.items():
                if isinstance(param_info, str):
                    prop_type = param_info
                    description = ""
                    optional = False
                elif isinstance(param_info, dict):
                    prop_type = param_info.get("type", "string")
                    description = param_info.get("description", "")
                    optional = param_info.get("optional", False)
                else:
                    # Unknown shape - coerce to string
                    prop_type = "string"
                    description = ""
                    optional = False
                
                properties_schema[param_name] = {
                    "type": prop_type,
                    "description": description,
                }
                
                # If JSON Schema 'required' present we already populated required list above.
                # Otherwise, treat as required unless explicitly optional.
                if not required:
                    if not optional:
                        required.append(param_name)
            
            tools_list.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties_schema,
                        "required": required,
                    },
                },
            })
        return tools_list

    async def _handle_retrieve_memory(self, query: str) -> str:
        """Handle retrieve_memory tool call."""
        logger.info(f"Retrieving memories for query: {query}")
        
        try:
            memories = self.memory_manager.retrieve_memories(
                query=query,
                top_k=5,
                session_id=self.current_session.session_id if self.current_session else None
            )
            
            if not memories:
                return "No relevant memories found."
            
            result = "Found relevant memories:\n\n"
            for i, mem in enumerate(memories, 1):
                result += f"{i}. [Score: {mem.score:.2f}] (Source: {mem.source})\n"
                result += f"{mem.content[:500]}...\n\n"
                
            return result
        except Exception as e:
            logger.error(f"Error retrieving memories: {e}")
            return f"Error retrieving memories: {str(e)}"
    
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
        
        Implements the core loop as defined in Planning.md Section "同步主循环 + TaskManager异步管理":
        - Synchronous main loop (調試友好 = easy to debug)
        - Long-running ops via TaskManager (asyncio.create_task) non-blocking
        - HITL confirmation for Zone C (立即確認 = immediate confirmation)
        - Full state persistence (完整持久化 = complete persistence)

        Args:
            session_id: Conversation session ID
            user_id: User identifier for auth checks
            channel_type: Communication channel (telegram/feishu/cli)
            message_input_fn: Async callable to receive user messages -> Message
            message_output_fn: Async callable to send response -> dict
            state: Optional state.json data to restore from (for agent restart)
        
        Loop Flow (Planning.md):
            用户输入 → Agent同步处理 → 检测耗时工具调用
              ↓
            工具不直接执行 → 交由TaskManager → asyncio.create_task()
              ↓
            工具立即返回 task_id（如"#001") → Agent继续循环
              ↓
            主循环每轮调用 poll_results() → 检查后台任务完成情况
              ↓
            任务完成 → Agent主动推送结果到用户
        
        Features:
            - ✅ Synchronous main loop (easy to debug, avoid event stream complexity)
            - ✅ Background tasks via TaskManager (asyncio.create_task, non-blocking)
            - ✅ HITL confirmation for Zone C operations (WAITING_FOR_AUTH state)
            - ✅ State persistence (state.json with active_tasks, message history)
            - ✅ Task resume on agent restart (load_from_state)
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
                        # Send completed task result to user
                        await message_output_fn({
                            "type": "task_completed",
                            "task_id": task_result.get("task_id"),
                            "status": task_result.get("status"),
                            "result": task_result.get("result"),
                            "error": task_result.get("error"),
                        })
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
                    # Zone C operations return with "requires_auth": True
                    if response.get("requires_auth"):
                        auth_request_id = response.get("auth_request_id")
                        pending_auth = response.get("pending_auth", {})

                        assistant_text = response.get("assistant_message") or response.get("response")
                        if assistant_text:
                            await message_output_fn({
                                "type": "assistant_message",
                                "response": assistant_text,
                                "tool_calls": response.get("tool_calls", []),
                                "tool_results": response.get("tool_results", []),
                            })
                        
                        # Send to user with auth buttons
                        await message_output_fn({
                            "type": "auth_request",
                            "auth_request_id": auth_request_id,
                            "tool_name": pending_auth.get("tool_name"),
                            "description": pending_auth.get("description"),
                            "diff_preview": pending_auth.get("diff_preview"),
                            "requires_approval": True,
                        })
                        logger.info(f"Auth request sent to user: {auth_request_id}")
                        
                        # ========== 4b. Await Auth Response OR New Message ==========
                        # We must listen to BOTH auth responses and new chat messages.
                        # If a new message arrives, we cancel the auth request.
                        if auth_response_fn:
                            logger.info(f"[DEBUG] Awaiting auth_response_fn for {auth_request_id}...")
                            
                            auth_task = asyncio.create_task(auth_response_fn(auth_request_id, 300.0))
                            msg_task = asyncio.create_task(message_input_fn())
                            
                            done, pending = await asyncio.wait(
                                [auth_task, msg_task], 
                                return_when=asyncio.FIRST_COMPLETED
                            )
                            
                            # Clean up pending tasks
                            for task in pending:
                                task.cancel()
                                
                            if msg_task in done:
                                # User sent a new message! Cancel auth.
                                logger.info("User sent new message during auth wait. Cancelling auth.")
                                self.pending_auth_requests.pop(auth_request_id, None)
                                self.state = AgentState.RUNNING
                                
                                # RECORD in history so agent remembers the interruption
                                self.message_history.append(Message(
                                    id=f"msg_{datetime.utcnow().timestamp()}_cancel",
                                    channel_type=channel_type,
                                    sender_id="system",
                                    sender_name="System",
                                    content="[System] The previous authorization request was cancelled because the user sent a new message."
                                ))
                                
                                await message_output_fn({
                                    "type": "response",
                                    "response": "⚠️ Auth cancelled by new input.",
                                    "tool_calls": [],
                                    "tool_results": [],
                                })
                                
                                # Process the new message immediately
                                new_msg = msg_task.result()
                                if new_msg:
                                    logger.info(f"Processing interrupting message: {new_msg.content[:50]}")
                                    response = await self.process_message(new_msg)
                                    
                                    # Very basic handling of the interrupting response
                                    # Realistically, this will just loop around naturally
                                    # but we output the immediate response here.
                                    await message_output_fn({
                                        "type": "response",
                                        "response": response.get("response", ""),
                                        "tool_calls": response.get("tool_calls", []),
                                        "tool_results": response.get("tool_results", []),
                                    })
                                continue
                                
                            else:
                                # Auth task finished!
                                auth_resp = auth_task.result()
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
                                    
                                    # Send the result back to user
                                    if approved and auth_result.get("status") == "approved":
                                        self.message_history.append(Message(
                                            id=f"msg_{datetime.utcnow().timestamp()}_auth_ok",
                                            channel_type=channel_type,
                                            sender_id="system",
                                            sender_name="System",
                                            content=f"[System] The authorization request was APPROVED. Tool Execution Result: {auth_result.get('result', 'OK')}"
                                        ))

                                        if resume_payload:
                                            await message_output_fn({
                                                "type": "assistant_message",
                                                "response": resume_payload.get("response", "✅ Operation approved."),
                                                "tool_calls": resume_payload.get("tool_calls", []),
                                                "tool_results": resume_payload.get("tool_results", []),
                                            })

                                            if resume_payload.get("requires_auth"):
                                                follow_auth = resume_payload.get("pending_auth", {})
                                                await message_output_fn({
                                                    "type": "auth_request",
                                                    "auth_request_id": resume_payload.get("auth_request_id"),
                                                    "tool_name": follow_auth.get("tool_name"),
                                                    "description": follow_auth.get("description"),
                                                    "diff_preview": follow_auth.get("diff_preview"),
                                                    "requires_approval": True,
                                                })
                                        else:
                                            success_msg = f"✅ Operation approved. Result: {auth_result.get('result', 'OK')}"
                                            await message_output_fn({
                                                "type": "response",
                                                "response": success_msg,
                                                "tool_calls": [],
                                                "tool_results": [],
                                            })
                                    else:
                                        reason = auth_result.get("error", "Rejected by user")
                                        self.message_history.append(Message(
                                            id=f"msg_{datetime.utcnow().timestamp()}_auth_fail",
                                            channel_type=channel_type,
                                            sender_id="system",
                                            sender_name="System",
                                            content=f"[System] The authorization request was REJECTED or FAILED. Error: {reason}"
                                        ))
                                        await message_output_fn({
                                            "type": "response",
                                            "response": f"❌ Operation {auth_result.get('status', 'rejected')}: {reason}",
                                            "tool_calls": [],
                                            "tool_results": [],
                                        })
                                else:
                                    # Timed out or no response
                                    logger.warning(f"Auth request {auth_request_id} timed out")
                                    self.pending_auth_requests.pop(auth_request_id, None)
                                    self.state = AgentState.RUNNING
                                    
                                    self.message_history.append(Message(
                                        id=f"msg_{datetime.utcnow().timestamp()}_auth_timeout",
                                        channel_type=channel_type,
                                        sender_id="system",
                                        sender_name="System",
                                        content="[System] The authorization request TIMED OUT. The operation was cancelled."
                                    ))
                                    
                                    await message_output_fn({
                                        "type": "response",
                                        "response": "⏰ Authorization request timed out. Operation cancelled.",
                                        "tool_calls": [],
                                        "tool_results": [],
                                    })
                        # If no auth_response_fn, the old behavior applies:
                        # agent stays in WAITING_FOR_AUTH and user can send a new message
                    else:
                        # Normal response, send to user
                        await message_output_fn({
                            "type": "response",
                            "response": response.get("response"),
                            "tool_calls": response.get("tool_calls", []),
                            "tool_results": response.get("tool_results", []),
                        })
                        logger.info("Response sent to user")
                    
                    # ========== 5. Persist State ==========
                    # Save to state.json after each message
                    # Ensures task recovery on crash/restart
                    state_snapshot = await self._save_state()
                    logger.debug(f"State persisted: {len(state_snapshot.get('active_tasks', {}))} active tasks")
                    
                except Exception as loop_error:
                    logger.error(f"Error in main loop: {loop_error}", exc_info=True)
                    await message_output_fn({
                        "type": "error",
                        "error": f"Loop error: {str(loop_error)}",
                    })
                    # Continue loop instead of breaking
        
        except Exception as e:
            logger.error(f"Fatal error in Agent.run(): {e}", exc_info=True)
            await message_output_fn({
                "type": "error",
                "error": f"Fatal: {str(e)}",
            })
        
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
        import json
        state_dict = {
            "version": "0.1",
            "agent_state": self.state.value,
            "last_save_time": datetime.utcnow().isoformat(),
            "message_history": [msg.to_dict() if hasattr(msg, 'to_dict') else str(msg) 
                               for msg in self.message_history],
            "compact_memory_snapshot": self.compact_memory_snapshot,
            # Serializing auth requests might be tricky if they contain complex types, 
            # simplest approach is to exclude or selectively serialize. For now, empty them for persistence
            # since auths shouldn't usually outlast a restart.
            "pending_auth_requests": {}, 
        }
        
        # Include TaskManager state if available
        if hasattr(self, 'task_manager') and self.task_manager:
            task_state = await self.task_manager.save_to_state()
            state_dict.update(task_state)
        else:
            state_dict["active_tasks"] = {}
            state_dict["completed_results"] = {}
            
        # Write to disk
        if hasattr(self, 'state_file') and self.state_file:
            # Assume workspace_root exists or default to current dir
            root = getattr(self, 'workspace_root', '.')
            path = os.path.join(root, self.state_file)
            try:
                # Write to temp file then rename for atomic save
                temp_path = f"{path}.tmp"
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(state_dict, f, ensure_ascii=False, indent=2)
                os.replace(temp_path, path)
                logger.info(f"[DEBUG] _save_state: saved {len(self.message_history)} messages to {path}")
            except Exception as e:
                logger.error(f"Failed to persist state to {path}: {e}")
        else:
            logger.warning(f"[DEBUG] _save_state: state_file not set! state_file={getattr(self, 'state_file', 'N/A')}")
        
        return state_dict
    
    async def load_state_from_disk(self) -> None:
        """Attempt to restore state from state.json on disk."""
        logger.info(f"[DEBUG] load_state_from_disk: state_file={getattr(self, 'state_file', 'N/A')}, workspace_root={getattr(self, 'workspace_root', 'N/A')}")
        if not hasattr(self, 'state_file') or not self.state_file:
            logger.warning(f"[DEBUG] load_state_from_disk: NO state_file configured, skipping")
            return
            
        root = getattr(self, 'workspace_root', '.')
        path = os.path.join(root, self.state_file)
        
        if not os.path.exists(path):
            logger.info(f"[DEBUG] load_state_from_disk: file {path} does not exist")
            return
            
        try:
            import json
            with open(path, 'r', encoding='utf-8') as f:
                state_dict = json.load(f)
            logger.info(f"[DEBUG] load_state_from_disk: loaded JSON from {path}, message_history has {len(state_dict.get('message_history', []))} entries")
            await self._restore_state(state_dict)
            logger.info(f"[DEBUG] load_state_from_disk: after _restore_state, self.message_history has {len(self.message_history)} entries")
            logger.info(f"Loaded persisted agent state from {path}")
        except Exception as e:
            logger.error(f"Failed to load state from {path}: {e}")
    
    async def _restore_state(self, state_dict: dict[str, Any]) -> None:
        """Restore agent state from dictionary (state.json).
        
        Restores:
        - Message history
        - TaskManager tasks (active + completed)
        - Pending auth requests
        """
        # Restore message history
        if "message_history" in state_dict:
            try:
                # Need to convert dicts back to Message objects if possible
                from ..types import Message
                history = []
                for msg_data in state_dict["message_history"]:
                    if isinstance(msg_data, dict):
                        history.append(Message.from_dict(msg_data))
                self.message_history = history
                logger.info(f"Restored {len(self.message_history)} messages from state")
            except Exception as e:
                logger.error(f"Error restoring message history: {e}")

        self.compact_memory_snapshot = state_dict.get("compact_memory_snapshot")
        
        # Restore TaskManager state
        if hasattr(self, 'task_manager') and self.task_manager:
            await self.task_manager.load_from_state(state_dict)
            logger.info("Restored TaskManager active tasks from state")
        
        # Restore pending auth requests
        if "pending_auth_requests" in state_dict:
            self.pending_auth_requests = state_dict["pending_auth_requests"]
            logger.info(f"Restored {len(self.pending_auth_requests)} pending auth requests")
