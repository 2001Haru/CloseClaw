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
from ..memory import MemoryFlushSession, MemoryFlushCoordinator, MemoryManager

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

    PREEMPTIVE_FLUSH_MARGIN = 0.05
    
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
        self.pending_auth_requests: dict[str, Any] = {}  # auth_id -> request
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

        # Trigger flush slightly before warning threshold to reduce hard-overflow retries.
        warning_threshold = getattr(cm, "warning_threshold", 0.75)
        preemptive_ratio = warning_threshold + self.PREEMPTIVE_FLUSH_MARGIN
        self.preemptive_flush_ratio = max(0.0, min(1.0, preemptive_ratio))
        
        self.message_compactor = MessageCompactor(
            summarize_window=config.context_management.summarize_window,
            active_window=config.context_management.active_window,
            chunk_size=config.context_management.chunk_size
        )
        
        # Phase 4 Step 2: Memory Flush Session - Automatic memory preservation
        self.memory_flush_session = MemoryFlushSession(workspace_root=workspace_root)
        self.memory_flush_coordinator = MemoryFlushCoordinator(self.memory_flush_session)
        
        # Phase 4 Step 3: Memory Manager - SQLite + Vector Search
        self.memory_manager = MemoryManager(workspace_root=workspace_root)
        
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
        """Process a single user message through the agent loop.
        
        REDESIGNED Phase 4: Enhanced Memory Flush with Early Token Detection
        
        New workflow:
        1. PRE-FLIGHT TOKEN CHECK: Before adding user message, check if already over threshold
        2. IF OVER THRESHOLD:
           - Do NOT add user message to history yet
           - Create flush phase: send history + system flush prompt to LLM
           - LLM extracts key information and saves to memory via write_memory_file
           - Compress history
           - Return completion status
           - Store user message for processing in next call
        3. IF UNDER THRESHOLD:
           - Add user message and process normally
        4. AFTER FLUSH (next process_message call):
           - Add the stored user message
           - Process normally with compressed history
        
        Returns: {
            "response": str,
            "tool_calls": list[ToolCall],
            "tool_results": list[ToolResult],
            "requires_auth": bool,
            "memory_flushed": bool (True if flush was executed)
        }
        """
        if not self.current_session:
            raise RuntimeError("No active session")
        
        # STATE LOCK: Clear auth if new message arrives
        if self.state == AgentState.WAITING_FOR_AUTH:
            logger.info(f"Clearing pending auth requests: user sent new message while WAITING_FOR_AUTH")
            self.pending_auth_requests.clear()
            self.state = AgentState.RUNNING
        
        # ========================================
        # PHASE 0: PRE-FLIGHT TOKEN DETECTION
        # ========================================
        # Check if we're already over threshold BEFORE adding new message
        logger.warning("\n" + "="*70)
        logger.warning("[PHASE 4 REDESIGNED] Pre-flight token check...")
        
        temp_messages = []
        system_message = {
            "role": "system",
            "content": self._build_system_prompt(),
        }
        temp_messages.append(system_message)
        
        # Add current history (not including new message yet)
        for msg in self.message_history:
            role = "user" if msg.sender_id != self.agent_id else "assistant"
            temp_messages.append({
                "role": role,
                "content": msg.content,
            })
        
        # Calculate tokens WITHOUT the new message
        current_token_count = self.context_manager.count_message_tokens(temp_messages)
        current_status, _ = self.context_manager.check_thresholds(current_token_count)
        current_ratio = self.context_manager.get_usage_ratio(current_token_count)
        
        logger.warning(f"[PRE-CHECK] Current history tokens: {current_token_count}/{self.context_manager.max_tokens} ({current_ratio*100:.1f}%) [Status: {current_status}]")
        
        # Now check if ADDING the new message would exceed threshold
        temp_messages_with_new = temp_messages.copy()
        temp_messages_with_new.append({
            "role": "user",
            "content": message.content,
        })
        
        new_total_tokens = self.context_manager.count_message_tokens(temp_messages_with_new)
        new_status, should_preflush = self.context_manager.check_thresholds(new_total_tokens)
        new_ratio = self.context_manager.get_usage_ratio(new_total_tokens)
        
        logger.warning(f"[PRE-CHECK] With new message:           {new_total_tokens}/{self.context_manager.max_tokens} ({new_ratio*100:.1f}%) [Status: {new_status}]")
        
        # ========================================
        # PHASE 1: DECIDE - FLUSH OR PROCESS?
        # ========================================
        if should_preflush or new_ratio >= self.preemptive_flush_ratio:
            logger.warning(f"[DECISION] Token threshold ({new_ratio*100:.1f}%) exceeded! Executing FLUSH FIRST with full context")
            logger.warning("="*70)
            
            # PHASE 1A: Execute flush with full conversation context
            # Do NOT add the user message yet - process flush first
            flush_result = await self._execute_memory_flush_with_context(self.message_history)
            
            if flush_result.get("success"):
                logger.warning(f"[FLUSH COMPLETE] History compressed from {len(self.message_history)} to {len(self.message_history)} messages")
                logger.warning(f"[CONTINUING] Now processing the user message with fresh history...")
                logger.warning("="*70 + "\n")
                
                # PHASE 2: NOW process the user message with flushed/compressed history
                self.message_history.append(message)
                
                # Now call LLM with the new message
                messages_for_llm = self._format_conversation_for_llm()
                tools_for_llm = self._format_tools_for_llm()
                
                logger.info("Calling LLM with user message (post-flush)...")
                try:
                    llm_response, tool_calls = await self.llm_provider.generate(
                        messages=messages_for_llm,
                        tools=tools_for_llm,
                        temperature=self.config.temperature,
                    )
                except Exception as e:
                    logger.error(f"LLM error: {e}")
                    self.state = AgentState.ERROR
                    return {
                        "response": f"Error calling LLM: {str(e)}",
                        "tool_calls": [],
                        "tool_results": [],
                        "requires_auth": False,
                        "memory_flushed": True,
                    }
                
                # Process tool calls normally
                tool_results = []
                if tool_calls:
                    logger.info(f"Processing {len(tool_calls)} tool calls...")
                    for tool_call in tool_calls:
                        result = await self._process_tool_call(tool_call)
                        tool_results.append(result)
                        if result.status == "auth_required":
                            self.state = AgentState.WAITING_FOR_AUTH
                            break
                
                # Save to history
                if llm_response:
                    self.message_history.append(Message(
                        id=f"msg_{datetime.utcnow().timestamp()}",
                        channel_type=self.current_session.channel_type if self.current_session else "unknown",
                        sender_id=self.agent_id,
                        sender_name="Agent",
                        content=llm_response,
                        tool_calls=tool_calls,
                        tool_results=tool_results,
                    ))
                
                return {
                    "response": llm_response or "OK",
                    "tool_calls": [tc.to_dict() for tc in tool_calls] if tool_calls else [],
                    "tool_results": [tr.to_dict() for tr in tool_results],
                    "requires_auth": False,
                    "memory_flushed": True,
                }
            else:
                logger.warning(f"[FLUSH FAILED] {flush_result.get('error', 'Unknown error')}")
                logger.warning("="*70 + "\n")
        
        # ========================================
        # PHASE 2: NORMAL PROCESSING (No flush needed)
        # ========================================
        logger.warning(f"[DECISION] Within threshold. Proceeding with normal message processing")
        logger.warning("="*70 + "\n")
        
        # Standard flow: add message and process
        self.message_history.append(message)
        
        # Format conversation for LLM
        messages_for_llm = self._format_conversation_for_llm()
        tools_for_llm = self._format_tools_for_llm()
        
        # Call LLM
        logger.info("Calling LLM...")
        try:
            llm_response, tool_calls = await self.llm_provider.generate(
                messages=messages_for_llm,
                tools=tools_for_llm,
                temperature=self.config.temperature,
            )
        except Exception as e:
            logger.error(f"LLM error: {e}")
            self.state = AgentState.ERROR
            return {
                "response": f"Error calling LLM: {str(e)}",
                "tool_calls": [],
                "tool_results": [],
                "requires_auth": False,
                "memory_flushed": False,
            }
        
        # Process tool calls if any
        tool_results = []
        pending_auth = None
        
        if tool_calls:
            logger.info(f"Processing {len(tool_calls)} tool calls...")
            for tool_call in tool_calls:
                result = await self._process_tool_call(tool_call)
                tool_results.append(result)
                
                # Check if Zone C operation requiring auth
                if result.status == "auth_required":
                    pending_auth = result.metadata
                    self.state = AgentState.WAITING_FOR_AUTH
                    break  # Stop processing further tools until auth
        
        # If auth required, return early but FIRST save to history!
        if pending_auth:
            self.message_history.append(Message(
                id=f"msg_{datetime.utcnow().timestamp()}",
                channel_type=self.current_session.channel_type if self.current_session else "unknown",
                sender_id=self.agent_id,
                sender_name="Agent",
                content=llm_response or "Executing tools... (Authorization Required)",
                tool_calls=tool_calls,
                tool_results=tool_results,
            ))
            return {
                "response": llm_response or "Awaiting authorization...",
                "tool_calls": [tc.to_dict() for tc in tool_calls] if tool_calls else [],
                "tool_results": [tr.to_dict() for tr in tool_results],
                "requires_auth": True,
                "auth_request_id": pending_auth.get("auth_request_id"),
                "pending_auth": pending_auth,
                "memory_flushed": False,
            }
        
        # Save assistant response to history
        if tool_calls and tool_results:
            self.message_history.append(Message(
                id=f"msg_{datetime.utcnow().timestamp()}",
                channel_type=self.current_session.channel_type if self.current_session else "unknown",
                sender_id=self.agent_id,
                sender_name="Agent",
                content=llm_response or "Executed tools.",
                tool_calls=tool_calls,
                tool_results=tool_results,
            ))
        elif llm_response:
            self.message_history.append(Message(
                id=f"msg_{datetime.utcnow().timestamp()}",
                channel_type=self.current_session.channel_type if self.current_session else "unknown",
                sender_id=self.agent_id,
                sender_name="Agent",
                content=llm_response,
            ))
            
        return {
            "response": llm_response or "OK",
            "tool_calls": [tc.to_dict() for tc in tool_calls] if tool_calls else [],
            "tool_results": [tr.to_dict() for tr in tool_results],
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
        
        # Add message history
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
            
            messages.append(msg_dict)
            
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
                        
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr.tool_call_id,
                        "content": content
                    })
        
        # Phase 4: Token counting and context management
        token_count = self.context_manager.count_message_tokens(messages)
        status, should_flush = self.context_manager.check_thresholds(token_count)
        
        context_report = self.context_manager.get_status_report(token_count)
        logger.info(f"[CONTEXT] Token usage: {context_report['usage_percentage']} ({token_count}/{self.context_manager.max_tokens}), Status: {status}")
        
        # Enhance system prompt with token usage information
        token_usage_info = f"\n\n[CONTEXT MONITOR] Current token usage: {token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})"
        system_message["content"] = system_content + token_usage_info
        
        # Phase 4 Step 2: Memory Flush - Detect flush need and mark for separate execution
        usage_ratio = self.context_manager.get_usage_ratio(token_count)
        if self.memory_flush_coordinator.mark_flush_pending(status, usage_ratio):
            logger.warning(f"[MEMORY_FLUSH] 🚨 Flush needed at {context_report['usage_percentage']} - will execute before next message")
            # DO NOT inject flush prompt here. Coordinator tracks pending state.
            # This allows Agent to finish current task without distraction.
        
        # If approaching critical threshold, apply message compaction
        if status == "CRITICAL":
            force_truncate = True
            
            compressed_messages, action = self.message_compactor.apply_compression_strategy(
                messages,
                token_count,
                usage_ratio,
                force=force_truncate
            )
            
            if action != "none":
                logger.warning(f"[CONTEXT_COMPACTION] Applied '{action}' compression. Original: {len(messages)} messages")
                messages = compressed_messages
                # Re-count tokens after compression
                token_count = self.context_manager.count_message_tokens(messages)
                status, needs_flush = self.context_manager.check_thresholds(token_count)
                context_report = self.context_manager.get_status_report(token_count)
                logger.info(f"[CONTEXT] After compression: {token_count}/{self.context_manager.max_tokens} tokens ({context_report['usage_percentage']})")
                
                # Update system prompt with new token count
                token_usage_info = f"\n\n[CONTEXT MONITOR] Current token usage: {token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})"
                messages[0]["content"] = self._build_system_prompt(token_usage_info)
                
                # CRITICAL FIX: Also trim message_history to prevent re-accumulation
                # Calculate how many message objects (from original message_history) to keep
                # Use aggressive trimming: active_window * 2 (not * 3) to ensure new messages won't exceed limit
                # Each conversation round typically = user msg + assistant msg
                target_history_size = max(self.message_compactor.active_window * 2, 5)  # At least keep 5 messages
                if len(self.message_history) > target_history_size:
                    old_size = len(self.message_history)
                    self.message_history = self.message_history[-target_history_size:]
                    logger.warning(f"[MEMORY_COMPACTION] Aggressively trimmed message_history from {old_size} → {len(self.message_history)} (target={target_history_size}) to prevent re-accumulation on next message")
        
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
    
    async def _execute_memory_flush_with_context(self, history_messages: list[Message]) -> dict:
        """Execute memory flush with FULL conversation context so agent can extract key info.
        
        IMPROVED APPROACH:
        - Agent sees the entire conversation history
        - Agent can identify and extract key information
        - Agent writes important information to memory via write_memory_file
        - Then we compress the history for next round
        
        Args:
            history_messages: Current message history to flush
            
        Returns:
            {"success": bool, "error": str, "files_saved": int}
        """
        logger.warning("[MEMORY_FLUSH] 🧠 Executing flush WITH FULL CONTEXT")
        logger.warning(f"[MEMORY_FLUSH]    Conversation has {len(history_messages)} messages to analyze")
        
        if not history_messages:
            logger.warning("[MEMORY_FLUSH] No history to flush")
            return {"success": True, "error": None, "files_saved": 0}
        
        # Build full message list WITH history (so agent sees what to extract from)
        messages = []
        
        # System message
        system_message = {
            "role": "system",
            "content": "You are an AI assistant preserving important conversation memory for future reference.",
        }
        messages.append(system_message)
        
        # Add complete history
        for msg in history_messages:
            role = "user" if msg.sender_id != self.agent_id else "assistant"
            messages.append({
                "role": role,
                "content": msg.content,
            })
        
        # Add flush instruction as FINAL USER message with complete context
        flush_instruction = self.memory_flush_session.create_flush_system_prompt()
        messages.append({
            "role": "user",  # User role to avoid duplicate system roles
            "content": flush_instruction,
        })
        
        logger.warning(f"[MEMORY_FLUSH]    Sending {len(messages)} messages to LLM (with full history)")
        logger.warning(f"[MEMORY_FLUSH]    LLM can now see: main conversation + flush instruction")
        
        # Get tools for LLM
        tools_for_llm = self._format_tools_for_llm()
        
        max_loops = 5
        files_saved = 0
        
        for loop_idx in range(max_loops):
            logger.warning(f"[MEMORY_FLUSH] 🔄 Loop {loop_idx+1}/{max_loops} - Calling LLM...")
            try:
                llm_response, tool_calls = await self.llm_provider.generate(
                    messages=messages,
                    tools=tools_for_llm,
                    temperature=0.3, # Allow slight creativity for synthesizing memory
                )
            except Exception as e:
                logger.error(f"[MEMORY_FLUSH] LLM flush call failed: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "files_saved": 0
                }
            
            # Record assistant response in temporary history
            assistant_message = {
                "role": "assistant",
                "content": llm_response or ""
            }
            if tool_calls:
                # Need to convert internal Object format to strictly matching OpenAI schema format
                # Using the identical logic the LLM provider mapping does internally (or close to it)
                formatted_tools = []
                for tc in tool_calls:
                    formatted_tools.append({
                        "id": tc.tool_id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments
                        }
                    })
                assistant_message["tool_calls"] = formatted_tools
            messages.append(assistant_message)
            
            logger.warning(f"[MEMORY_FLUSH] 📨 LLM Response: {llm_response[:100] if llm_response else '(empty)'}...")
            
            # End loop if LLM indicated it has finished
            if self.memory_flush_session.check_for_silent_reply(llm_response):
                logger.warning(f"[MEMORY_FLUSH] ✅ [SILENT_REPLY] marker detected. Terminating loop.")
                break
                
            flush_tool_calls = tool_calls if tool_calls else []
            if flush_tool_calls:
                logger.warning(f"[MEMORY_FLUSH] 🔧 Processing {len(flush_tool_calls)} tool call(s):")
                for i, tool_call in enumerate(flush_tool_calls, 1):
                    logger.warning(f"[MEMORY_FLUSH]    ({i}/{len(flush_tool_calls)}) {tool_call.name}...")
                    try:
                        result = await self._process_tool_call(tool_call)
                        
                        # Record tool result in temporary history
                        content_val = ""
                        if result.status == "success":
                            content_val = json.dumps(result.result) if not isinstance(result.result, str) else result.result
                        else:
                            content_val = f"Error: {result.error}"

                        # Hard limit to prevent token overflows
                        MAX_RESULT_CHARS = 10000
                        if len(content_val) > MAX_RESULT_CHARS:
                            logger.warning(f"[MEMORY_FLUSH] Truncating tool output from {len(content_val)} to {MAX_RESULT_CHARS} chars")
                            content_val = content_val[:MAX_RESULT_CHARS] + f"\n\n... [Output truncated because it exceeded {MAX_RESULT_CHARS} characters]"

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.tool_id,
                            "content": content_val
                        })
                        logger.info(f"[MEMORY_FLUSH] Append tool message. Call ID: {tool_call.tool_id}, output size: {len(content_val)}")
                        
                        if result.status == "success":
                            logger.warning(f"[MEMORY_FLUSH]        ✅ SUCCESS")
                            
                            # Phase 4 Step 3: SQLite logging for write_memory_file only
                            if tool_call.name == "write_memory_file":
                                files_saved += 1
                                try:
                                    content = tool_call.arguments.get("content", "")
                                    filename = tool_call.arguments.get("filename", "unknown.md")
                                    
                                    if content:
                                        self.memory_manager.add_memory(
                                            content=content,
                                            source=f"file:{filename}",
                                            session_id=self.current_session.session_id if self.current_session else "unknown",
                                            metadata={"filename": filename, "flush_type": "context_aware"}
                                        )
                                        logger.warning(f"[MEMORY_FLUSH]        🧠 Also indexed to SQLite MemoryManager")
                                except Exception as e:
                                    logger.error(f"[MEMORY_FLUSH]        ❌ Failed to index memory to SQLite: {e}")
                                    
                    except Exception as e:
                        logger.error(f"[MEMORY_FLUSH]        ❌ Tool execution exception: {e}")
            else:
                # No tool calls and no silent reply? Break loop. Let's force completion
                logger.warning(f"[MEMORY_FLUSH] ⚠️  No tool calls and no [SILENT_REPLY]. Terminating loop.")
                break
        else:
            logger.warning(f"[MEMORY_FLUSH] ⚠️ Reached maximum iterations ({max_loops}). Terminating loop.")
            
        logger.warning(f"[MEMORY_FLUSH] 💾 Total files saved: {files_saved}")
            
        # Now compress the history
        if history_messages:
            logger.warning(f"[MEMORY_FLUSH] 📦 Compressing history from {len(self.message_history)} messages...")
            self.message_history = self.message_history[-5:] if len(self.message_history) > 5 else self.message_history
            logger.warning(f"[MEMORY_FLUSH]    Compressed to {len(self.message_history)} messages")
        
        return {
            "success": True,
            "error": None,
            "files_saved": files_saved
        }
    
    async def _execute_memory_flush_standalone(self) -> None:
        """Execute memory flush as a nested sub-loop (read before write).
        
        Phase 4: This is called BEFORE processing a user message that would trigger flush.
        We provide the LLM with a temporary copy of the message history, inject the flush prompt,
        and run a mini-loop allowing it to look up existing memories before saving.
        """
        logger.warning("[MEMORY_FLUSH] 🚀 Starting standalone flush execution (Nested Loop)...")
        
        import copy
        # Create a deep copy of current history to provide context
        temp_messages = copy.deepcopy(self.message_history)
        
        # Append the flush prompt
        temp_messages.append({
            "role": "user",
            "content": self.memory_flush_session.create_flush_system_prompt()
        })
        
        tools_for_llm = self._format_tools_for_llm()
        
        max_loops = 5
        
        for loop_idx in range(max_loops):
            logger.warning(f"[MEMORY_FLUSH] 🔄 Loop {loop_idx+1}/{max_loops} - Calling LLM...")
            try:
                llm_response, tool_calls = await self.llm_provider.generate(
                    messages=temp_messages,
                    tools=tools_for_llm,
                    temperature=0.3, # Allow slight creativity for synthesizing memory
                )
            except Exception as e:
                logger.error(f"[MEMORY_FLUSH] LLM flush call failed: {e}")
                return
            
            # Record assistant response in temporary history
            assistant_message = {
                "role": "assistant",
                "content": llm_response or ""
            }
            if tool_calls:
                formatted_tools = []
                for tc in tool_calls:
                    formatted_tools.append({
                        "id": tc.tool_id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments
                        }
                    })
                assistant_message["tool_calls"] = formatted_tools
            temp_messages.append(assistant_message)
            
            logger.warning(f"[MEMORY_FLUSH] 📨 LLM Response: {llm_response[:100] if llm_response else '(empty)'}...")
            
            # End loop if LLM indicated it has finished
            if self.memory_flush_session.check_for_silent_reply(llm_response):
                logger.warning(f"[MEMORY_FLUSH] ✅ [SILENT_REPLY] marker detected. Terminating loop.")
                break
                
            flush_tool_calls = tool_calls if tool_calls else []
            if flush_tool_calls:
                logger.warning(f"[MEMORY_FLUSH] 🔧 Processing {len(flush_tool_calls)} tool call(s):")
                for i, tool_call in enumerate(flush_tool_calls, 1):
                    logger.warning(f"[MEMORY_FLUSH]    ({i}/{len(flush_tool_calls)}) {tool_call.name}...")
                    try:
                        result = await self._process_tool_call(tool_call)
                        
                        # Record tool result in temporary history
                        content_val = ""
                        if result.status == "success":
                            content_val = json.dumps(result.result) if not isinstance(result.result, str) else result.result
                        else:
                            content_val = f"Error: {result.error}"

                        # Hard limit to prevent token overflows
                        MAX_RESULT_CHARS = 10000
                        if len(content_val) > MAX_RESULT_CHARS:
                            logger.warning(f"[MEMORY_FLUSH] Truncating tool output from {len(content_val)} to {MAX_RESULT_CHARS} chars")
                            content_val = content_val[:MAX_RESULT_CHARS] + f"\n\n... [Output truncated because it exceeded {MAX_RESULT_CHARS} characters]"

                        temp_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.tool_id,
                            "content": content_val
                        })
                        logger.info(f"[MEMORY_FLUSH] Append tool message. Call ID: {tool_call.tool_id}, output size: {len(content_val)}")
                        
                        if result.status == "success":
                            logger.warning(f"[MEMORY_FLUSH]        ✅ SUCCESS")
                            
                            # Phase 4 Step 3: SQLite logging for write_memory_file only
                            if tool_call.name == "write_memory_file":
                                try:
                                    content = tool_call.arguments.get("content", "")
                                    filename = tool_call.arguments.get("filename", "unknown.md")
                                    
                                    if content:
                                        self.memory_manager.add_memory(
                                            content=content,
                                            source=f"file:{filename}",
                                            session_id=self.current_session.session_id if self.current_session else "unknown",
                                            metadata={"filename": filename, "flush_type": "standalone"}
                                        )
                                        logger.warning(f"[MEMORY_FLUSH]        🧠 Also indexed to SQLite MemoryManager")
                                except Exception as e:
                                    logger.error(f"[MEMORY_FLUSH]        ❌ Failed to index memory to SQLite: {e}")
                                    
                        else:
                            logger.warning(f"[MEMORY_FLUSH]        ⚠️  {result.status}")
                            if result.error:
                                logger.warning(f"[MEMORY_FLUSH]        Error: {result.error}")
                    except Exception as e:
                        logger.error(f"[MEMORY_FLUSH]        ❌ Tool execution exception: {e}")
            else:
                # No tool calls and no silent reply? Break loop. Let's force completion
                logger.warning(f"[MEMORY_FLUSH] ⚠️  No tool calls and no [SILENT_REPLY]. Terminating loop.")
                break
        else:
            logger.warning(f"[MEMORY_FLUSH] ⚠️ Reached maximum iterations ({max_loops}). Terminating loop.")
        
        # Wait for files to be written
        import time
        time.sleep(0.2)
        
        # Collect saved files
        saved_files = self.memory_flush_session.collect_saved_memories()
        logger.warning(f"[MEMORY_FLUSH] 📁 Collected {len(saved_files)} memory file(s)")
        
        # Record flush event
        session_id = self.memory_flush_coordinator.last_flush_session_id
        current_usage_ratio = self.context_manager.get_usage_ratio(
            self.context_manager.token_count
        )
        self.memory_flush_session.record_flush_event(
            user_id=self.current_session.user_id if self.current_session else "system",
            session_id=session_id,
            saved_files=saved_files,
            context_ratio=current_usage_ratio,
            audit_logger=self.audit_logger
        )
        
        # Clear history and pending flag
        logger.warning(f"[MEMORY_FLUSH] 🗑️  Compressing history from {len(self.message_history)} messages...")
        self.message_history = self.message_history[-5:] if len(self.message_history) > 5 else self.message_history
        logger.warning(f"[MEMORY_FLUSH]    Compressed to {len(self.message_history)} messages")
        self.memory_flush_coordinator.clear_pending_flush()
        
        logger.warning(f"[MEMORY_FLUSH] ✅ Flush complete - {len(saved_files)} files saved")
        
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
                                    
                                    # Send the result back to user
                                    if approved and auth_result.get("status") == "approved":
                                        success_msg = f"✅ Operation approved. Result: {auth_result.get('result', 'OK')}"
                                        self.message_history.append(Message(
                                            id=f"msg_{datetime.utcnow().timestamp()}_auth_ok",
                                            channel_type=channel_type,
                                            sender_id="system",
                                            sender_name="System",
                                            content=f"[System] The authorization request was APPROVED. Tool Execution Result: {auth_result.get('result', 'OK')}"
                                        ))
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
        
        # Restore TaskManager state
        if hasattr(self, 'task_manager') and self.task_manager:
            await self.task_manager.load_from_state(state_dict)
            logger.info("Restored TaskManager active tasks from state")
        
        # Restore pending auth requests
        if "pending_auth_requests" in state_dict:
            self.pending_auth_requests = state_dict["pending_auth_requests"]
            logger.info(f"Restored {len(self.pending_auth_requests)} pending auth requests")
