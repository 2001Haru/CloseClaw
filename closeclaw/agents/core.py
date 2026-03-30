"""Agent core implementation."""

import logging
import os
import json
from typing import Any, Awaitable, Callable, Optional, Protocol
from datetime import datetime, timezone
from pathlib import Path



from ..types import (
    Agent, AgentConfig, Session, Tool, Message,
    AgentState, ToolCall, ToolResult, ToolType
)
from ..middleware import MiddlewareChain
from ..tools.adaptation import ToolAdaptationLayer
from ..safety import AuditLogger
from ..context import ContextManager, MessageCompactor
from ..memory import MemoryFlushCoordinator, MemoryFlushSession, MemoryManager
from ..memory.workspace_layout import (
    DEFAULT_AUDIT_LOG_REL,
    ensure_workspace_memory_layout,
    migrate_legacy_memory_artifacts,
)
from ..orchestrator import (
    AfterObserveHook,
    BeforePlanHook,
    OrchestratorEngine,
    PlanPolicy,
    ProgressPolicy,
    PostActSafetyGuard,
    PreActBudgetGuard,
    PreActContextGuard,
    RunState,
)
from ..services import AuthService, BackgroundTaskService, ContextService, OrchestratorService, PlanningService, PromptBuilder, RuntimeLoopService, SkillsLoader, StateService, ToolExecutionService, ToolSchemaService

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
        self.workspace_root = os.path.abspath(workspace_root)
        self.repo_root = str(Path(__file__).resolve().parents[2])
        self.admin_user_id = admin_user_id
        self.state_file = state_file

        ensure_workspace_memory_layout(self.workspace_root)
        migrate_legacy_memory_artifacts(self.workspace_root)
        
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
        configured_audit_path = getattr(getattr(config, "safety", None), "audit_log_path", DEFAULT_AUDIT_LOG_REL)
        if configured_audit_path in {"", "audit.log"}:
            configured_audit_path = DEFAULT_AUDIT_LOG_REL
        if os.path.isabs(configured_audit_path):
            audit_log_path = configured_audit_path
        else:
            audit_log_path = os.path.join(self.workspace_root, configured_audit_path)
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
        self.memory_flush_session = MemoryFlushSession(workspace_root=self.workspace_root)
        self.memory_flush_coordinator = MemoryFlushCoordinator(self.memory_flush_session)
        
        # Phase 4 Step 3: Memory Manager - SQLite + Vector Search
        self.memory_manager = MemoryManager(workspace_root=self.workspace_root)

        # Phase 5: Single-loop orchestrator (MVP)
        self._phase5_auth_paused_runs: dict[str, RunState] = {}
        orchestrator_cfg = self.config.metadata.get("orchestrator", self.config.metadata.get("phase5", {}))
        progress_policy = ProgressPolicy(no_progress_limit=int(orchestrator_cfg.get("no_progress_limit", 2)))
        self.orchestrator_service = OrchestratorService(
            config=self.config,
            planning_service=PlanningService(llm_provider=self.llm_provider),
            context_service=None,  # set after context_service created
            runtime_loop_service=RuntimeLoopService(),
            progress_policy=progress_policy,
            plan_policy=PlanPolicy(),
            orchestrator_engine=OrchestratorEngine(),
            orchestrator_guards=[
                PreActBudgetGuard(),
                PreActContextGuard(
                    pre_act_callback=lambda state, action: self.orchestrator_service.pre_act_context_guard(state, action, self)
                ),
                PostActSafetyGuard(),
            ],
            orchestrator_hooks=[
                BeforePlanHook(),
                AfterObserveHook(),
            ],
            memory_flush_coordinator=self.memory_flush_coordinator,
        )
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
        self.background_task_service = BackgroundTaskService()
        self.state_service = StateService(
            workspace_root_getter=lambda: self.workspace_root,
            state_file_getter=lambda: self.state_file,
            task_manager_getter=lambda: getattr(self, "task_manager", None),
        )
        memory_index_cfg = self.config.metadata.get("memory_index", {})
        lazy_sync_budget = max(
            1,
            int(memory_index_cfg.get("lazy_sync_max_files_per_query", 3)),
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
            lazy_sync_max_files_per_query=lazy_sync_budget,
        )
        self.orchestrator_service.context_service = self.context_service
        self._runtime_message_output_fn: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None
        self.skills_loader = SkillsLoader(
            workspace=Path(self.workspace_root),
            builtin_skills_dir=Path(self.repo_root) / "closeclaw" / "skills",
        )
        self.prompt_builder = PromptBuilder(
            config=self.config,
            workspace_root=self.workspace_root,
            repo_root=self.repo_root,
            tools=self.tools,
            skills_loader=self.skills_loader,
            context_service=self.context_service,
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

        # Keep TaskManager handlers in sync for tools that may be routed to background.
        if hasattr(self, "task_manager") and self.task_manager and getattr(tool, "handler", None):
            self.task_manager.register_tool_handler(tool.name, tool.handler)
        
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

    def _resolve_work_timezone(self) -> tuple[Any, str]:
        """Resolve configured work timezone from metadata."""
        return self.prompt_builder.resolve_work_timezone()

    async def _process_message_v2_orchestrated(self, message: Message) -> dict[str, Any]:
        """Phase 5 P1 orchestrator flow — delegated to OrchestratorService."""
        return await self.orchestrator_service.run_turn(message, self)

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

    async def _phase5_resume_after_auth(self,
                                        auth_request_id: str,
                                        auth_result: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Resume a paused Phase5 run after an auth decision."""
        return await self.orchestrator_service.resume_after_auth(auth_request_id, auth_result, self)
    
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
        """Build multi-layer system prompt with project context and work information."""
        return self.prompt_builder.build(suffix)

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
        external_specs = self.tool_execution_service.list_external_specs()
        return self.tool_schema_service.format_tools_for_llm([*self.tools.values(), *external_specs])

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
    
    def set_task_manager(self, task_manager: Any) -> None:
        """Set the background task manager."""
        self.task_manager = task_manager
        self.background_task_service.attach(task_manager, self.tools)
        logger.info("TaskManager integrated with AgentCore")
    
    async def poll_background_tasks(self) -> list[dict[str, Any]]:
        """Poll for completed background tasks from TaskManager."""
        return await self.background_task_service.poll()
    
    async def create_background_task(self, 
                                    tool_name: str, 
                                    arguments: dict[str, Any]) -> str:
        """Create a background task for long-running tool execution."""
        return await self.background_task_service.create(tool_name, arguments)
    
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
        self._runtime_message_output_fn = message_output_fn
        
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
                            reason=pending_auth.get("reason"),
                            auth_mode=pending_auth.get("auth_mode"),
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
                    # Recovery save: Ensure partial turn progress or errors are saved
                    try:
                        await self._save_state()
                        logger.debug("State persevered after exception.")
                    except Exception as save_err:
                        logger.error(f"Failed to save state during error recovery: {save_err}")
                    # Continue loop instead of breaking
        
        except Exception as e:
            logger.error(f"Fatal error in Agent.run(): {e}", exc_info=True)
            await self.runtime_loop_service.emit_error(
                message_output_fn,
                error=f"Fatal: {str(e)}",
            )
        
        finally:
            # Cleanup
            self._runtime_message_output_fn = None
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


