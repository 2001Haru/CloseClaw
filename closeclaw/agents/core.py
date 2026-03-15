"""Agent core implementation."""

import asyncio
import logging
from typing import Any, Optional, Protocol
from datetime import datetime

from ..types import (
    Agent, AgentConfig, Session, Tool, Message, 
    AgentState, Zone, ToolCall, ToolResult
)
from ..middleware import MiddlewareChain
from ..tools.adaptation import ToolAdaptationLayer

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
    
    def __init__(self,
                 agent_id: str,
                 llm_provider: LLMProvider,
                 config: AgentConfig,
                 workspace_root: str,
                 admin_user_id: Optional[str] = None):
        """Initialize agent core.
        
        Args:
            agent_id: Unique agent identifier
            llm_provider: LLM implementation
            config: Agent configuration
            workspace_root: Root directory for file operations (sandboxing)
            admin_user_id: User ID authorized for HITL approval
        """
        self.agent_id = agent_id
        self.llm_provider = llm_provider
        self.config = config
        self.workspace_root = workspace_root
        self.admin_user_id = admin_user_id
        
        self.state = AgentState.IDLE
        self.current_session: Optional[Session] = None
        self.tools: dict[str, Tool] = {}
        self.message_history: list[Message] = []
        self.pending_auth_requests: dict[str, Any] = {}  # auth_id -> request
        self.middleware_chain: Optional[MiddlewareChain] = None
        self.tool_adaptation_layer = ToolAdaptationLayer()  # Phase 2: Tool routing
        
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
        """Start a new conversation session."""
        session = Session(
            session_id=session_id,
            user_id=user_id,
            channel_type=channel_type,
        )
        self.current_session = session
        self.message_history = []
        self.state = AgentState.RUNNING
        logger.info(f"Started session {session_id} for user {user_id}")
        return session
        
    async def process_message(self, message: Message) -> dict[str, Any]:
        """Process a single user message through the agent loop.
        
        Returns: {
            "response": str,
            "tool_calls": list[ToolCall],
            "tool_results": list[ToolResult],
            "requires_auth": bool,
            "auth_request_id": str (if auth required)
        }
        """
        if not self.current_session:
            raise RuntimeError("No active session")
        
        # STATE LOCK: If we were waiting for auth and get a new user message,
        # interpret it as cancellation of the previous dangerous operation.
        # This prevents "zombie requests" and allows users to interrupt operations.
        if self.state == AgentState.WAITING_FOR_AUTH:
            logger.info(f"Clearing pending auth requests: user sent new message while WAITING_FOR_AUTH")
            self.pending_auth_requests.clear()
            self.state = AgentState.RUNNING
            
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
        
        # If auth required, return early
        if pending_auth:
            return {
                "response": llm_response or "Awaiting authorization...",
                "tool_calls": [tc.to_dict() for tc in tool_calls] if tool_calls else [],
                "tool_results": [tr.to_dict() for tr in tool_results],
                "requires_auth": True,
                "auth_request_id": pending_auth.get("auth_request_id"),
                "pending_auth": pending_auth,
            }
        
        return {
            "response": llm_response or "OK",
            "tool_calls": [tc.to_dict() for tc in tool_calls] if tool_calls else [],
            "tool_results": [tr.to_dict() for tr in tool_results],
            "requires_auth": False,
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
            
            if auth_result["status"] == "blocked":
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
        if user_id != self.admin_user_id:
            logger.warning(f"Unauthorized auth approval attempt from {user_id}")
            return {
                "auth_request_id": auth_request_id,
                "status": "error",
                "error": "Not authorized to approve",
            }
        
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
        
        try:
            result = await tool.handler(**arguments)
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
    
    def _format_conversation_for_llm(self) -> list[dict[str, str]]:
        """Format conversation history for LLM input."""
        messages = []
        
        # Add system prompt
        if self.config.system_prompt:
            messages.append({
                "role": "system",
                "content": self.config.system_prompt,
            })
        
        # Add message history
        for msg in self.message_history:
            role = "user" if msg.sender_id != self.agent_id else "assistant"
            messages.append({
                "role": role,
                "content": msg.content,
            })
        
        return messages
    
    def _format_tools_for_llm(self) -> list[dict[str, Any]]:
        """Format registered tools for LLM function calling.
        
        Outputs OpenAI-standard format:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        }
        """
        tools_list = []
        for tool in self.tools.values():
            # Build JSON Schema parameters from tool.parameters
            properties = {}
            required = []
            
            for param_name, param_info in (tool.parameters or {}).items():
                prop = {
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", ""),
                }
                properties[param_name] = prop
                
                # Treat all params as required unless explicitly optional
                if not param_info.get("optional", False):
                    required.append(param_name)
            
            tools_list.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return tools_list
    
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
                            "diff_preview": pending_auth.get("diff_preview"),  # Structured diff
                            "requires_approval": True,
                        })
                        logger.info(f"Auth request sent to user: {auth_request_id}")
                        # Agent now in WAITING_FOR_AUTH state
                        # User responds via approve_auth_request() → back to RUNNING
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
        """Save agent state to dict (for persistence to state.json).
        
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
        state_dict = {
            "version": "0.1",
            "agent_state": self.state.value,
            "last_save_time": datetime.utcnow().isoformat(),
            "message_history": [msg.to_dict() if hasattr(msg, 'to_dict') else str(msg) 
                               for msg in self.message_history],
            "pending_auth_requests": self.pending_auth_requests,
        }
        
        # Include TaskManager state if available
        if hasattr(self, 'task_manager') and self.task_manager:
            task_state = await self.task_manager.save_to_state()
            state_dict.update(task_state)
        else:
            state_dict["active_tasks"] = {}
            state_dict["completed_results"] = {}
        
        return state_dict
    
    async def _restore_state(self, state_dict: dict[str, Any]) -> None:
        """Restore agent state from persistence (state.json).
        
        Restores:
        - Message history
        - TaskManager tasks (active + completed)
        - Pending auth requests
        """
        # Restore message history
        if "message_history" in state_dict:
            # Simple restoration - full implementation depends on Message.from_dict()
            logger.info(f"Restored {len(state_dict['message_history'])} messages from state")
        
        # Restore TaskManager state
        if hasattr(self, 'task_manager') and self.task_manager:
            await self.task_manager.load_from_state(state_dict)
            logger.info("Restored TaskManager active tasks from state")
        
        # Restore pending auth requests
        if "pending_auth_requests" in state_dict:
            self.pending_auth_requests = state_dict["pending_auth_requests"]
            logger.info(f"Restored {len(self.pending_auth_requests)} pending auth requests")
