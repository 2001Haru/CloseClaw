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
        
    def register_tool(self, tool: Tool) -> None:
        """Register a tool with the agent."""
        self.tools[tool.name] = tool
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
        
        Returns a ToolResult with status:
        - "success": Tool executed successfully
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
        
        # Execute tool
        try:
            logger.info(f"Executing tool: {tool_call.name}")
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
        """Format registered tools for LLM function calling."""
        tools_list = []
        for tool in self.tools.values():
            tools_list.append({
                "name": tool.name,
                "description": tool.description,
                "zone": tool.zone.value,
                "parameters": tool.parameters,
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
