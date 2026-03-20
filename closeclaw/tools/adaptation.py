"""Tool adaptation layer for detecting and handling long-running operations.

This layer sits between Agent and tool execution, implementing the strategy:
  - Mark tools with estimated execution time
  - Detect when LLM calls a long-running tool
  - Automatically route to TaskManager instead of direct execution
  - Return task_id to user immediately (non-blocking)

From Planning.md Phase 2:
    "Tool immediately returns task_id (e.g. '#001') -> Agent continues loop"
    (Tool returns task_id immediately -> Agent continues loop)
"""

import logging
from typing import Any, Optional, Callable
from enum import Enum

from ..types import Tool, ToolCall, ToolResult, ToolType

logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    """How a tool should be executed."""
    SYNC = "sync"         # Direct execution (fast, < 1s)
    ASYNC_BG = "async_bg"  # Background via TaskManager (slow, > 1s)


class ToolMetadata:
    """Enhanced tool metadata for adaptation decisions."""
    
    def __init__(self,
                 tool: Tool,
                 estimated_duration_seconds: float = 1.0,
                 execution_mode: ExecutionMode = ExecutionMode.SYNC):
        """
        Args:
            tool: The Tool definition
            estimated_duration_seconds: Expected execution time (for routing decision)
            execution_mode: How to execute (sync vs background)
        """
        self.tool = tool
        self.estimated_duration_seconds = estimated_duration_seconds
        self.execution_mode = execution_mode
    
    def should_use_background_task(self) -> bool:
        """Decide if this tool should run in background.
        
        Decision logic:
        - If execution_mode is ASYNC_BG: always background
        - If estimated_duration_seconds > 2.0: background
        - Otherwise: sync
        """
        if self.execution_mode == ExecutionMode.ASYNC_BG:
            return True
        
        if self.estimated_duration_seconds > 2.0:
            return True
        
        return False


class ToolAdaptationLayer:
    """Intelligent tool routing (sync vs background).
    
    Maintains a mapping of tools to their metadata and decides how to execute them.
    
    Usage:
        adapter = ToolAdaptationLayer()
        adapter.register_tool_metadata(web_search_tool, 
                                       estimated_duration_seconds=3.0)
        
        # Later, when processing tool call:
        result = await adapter.execute_tool_call(
            tool_call=tool_call,
            available_tools={...},
            task_manager=task_manager,
            direct_executor=agent._process_tool_call
        )
    """
    
    def __init__(self):
        """Initialize adaptation layer."""
        self._tool_metadata: dict[str, ToolMetadata] = {}
        self._long_running_tool_types = {
            ToolType.WEBSEARCH,    # Web searches can take 5-30s
            ToolType.SHELL,        # Shell commands can take minutes
        }
    
    def register_tool_metadata(self,
                              tool: Tool,
                              estimated_duration_seconds: float = 1.0,
                              execution_mode: Optional[ExecutionMode] = None) -> None:
        """Register a tool with metadata for adaptation decisions.
        
        Args:
            tool: The Tool to register
            estimated_duration_seconds: Estimated execution time
            execution_mode: Override execution mode (if None, auto-decide)
        """
        mode = execution_mode
        
        # Auto-decide execution mode if not provided
        if mode is None:
            # If tool type is known to be slow, use async
            if tool.type in self._long_running_tool_types:
                mode = ExecutionMode.ASYNC_BG
            # If estimated duration is long, use async
            elif estimated_duration_seconds > 2.0:
                mode = ExecutionMode.ASYNC_BG
            else:
                mode = ExecutionMode.SYNC
        
        metadata = ToolMetadata(
            tool=tool,
            estimated_duration_seconds=estimated_duration_seconds,
            execution_mode=mode,
        )
        
        self._tool_metadata[tool.name] = metadata
        logger.info(f"Registered tool: {tool.name} (mode={mode.value}, duration~{estimated_duration_seconds}s)")
    
    def get_tool_metadata(self, tool_name: str) -> Optional[ToolMetadata]:
        """Get metadata for a tool."""
        return self._tool_metadata.get(tool_name)
    
    async def execute_tool_call(self,
                               tool_call: ToolCall,
                               available_tools: dict[str, Tool],
                               task_manager: Any = None,
                               direct_executor: Optional[Callable] = None) -> ToolResult:
        """Route tool call to appropriate executor (sync or async).
        
        Args:
            tool_call: The tool call to execute
            available_tools: Dictionary of available tools
            task_manager: TaskManager instance (required for async routing)
            direct_executor: Async function to execute tools directly
                           Signature: async def executor(ToolCall) -> ToolResult
        
        Returns:
            ToolResult with status and metadata
        
        Logic:
            1. Get tool metadata
            2. Check if should use background task
            3. If yes:
               - Call task_manager.create_task()
               - Return ToolResult with task_id in result
            4. If no:
               - Call direct_executor()
               - Return result directly
        """
        tool_name = tool_call.name
        tool = available_tools.get(tool_name)
        
        if not tool:
            return ToolResult(
                tool_call_id=tool_call.tool_id,
                status="error",
                result=None,
                error=f"Tool '{tool_name}' not found",
            )
        
        # Get tool metadata (create default if not registered)
        metadata = self.get_tool_metadata(tool_name)
        if not metadata:
            # Estimate based on tool type
            estimated_seconds = self._estimate_duration(tool)
            metadata = ToolMetadata(
                tool=tool,
                estimated_duration_seconds=estimated_seconds,
            )
            logger.debug(f"Auto-classified tool: {tool_name} ({estimated_seconds}s)")
        
        # Decide execution mode
        use_background = metadata.should_use_background_task()
        
        if use_background and task_manager:
            logger.info(f"Route to background: {tool_name} (est. {metadata.estimated_duration_seconds}s)")
            
            # Create background task
            task_id = await task_manager.create_task(
                tool_name=tool_name,
                arguments=tool_call.arguments,
            )
            
            logger.info(f"Background task created: {task_id}")
            
            # Return task_id to caller
            result = ToolResult(
                tool_call_id=tool_call.tool_id,
                status="task_created",
                result={
                    "task_id": task_id,
                    "tool": tool_name,
                    "message": f"Long-running task created: {task_id}. Check progress with: closeclaw task {task_id}",
                },
            )
            result.metadata = {
                "task_id": task_id,
                "routing": "background",
            }
            return result
        
        else:
            logger.info(f"Direct execution: {tool_name} (est. {metadata.estimated_duration_seconds}s)")
            
            # Execute directly (sync mode)
            if not direct_executor:
                return ToolResult(
                    tool_call_id=tool_call.tool_id,
                    status="error",
                    result=None,
                    error="Direct executor not provided",
                )
            
            # Call the direct executor
            result = await direct_executor(tool_call)
            
            if result.status == "success":
                logger.info(f"Direct execution completed: {tool_name}")
            else:
                logger.warning(f"Direct execution failed: {tool_name} ({result.status})")
            
            result.metadata = {
                "routing": "direct",
                "execution_mode": "sync",
            }
            return result
    
    def _estimate_duration(self, tool: Tool) -> float:
        """Estimate execution duration based on tool type.
        
        Returns:
            Estimated duration in seconds
        """
        # Default estimates by tool type
        type_estimates = {
            ToolType.WEBSEARCH: 3.0,   # Web search typically 3-10s
            ToolType.SHELL: 5.0,       # Shell can vary, assume longer
            ToolType.FILE: 0.5,        # File ops usually fast
        }
        
        return type_estimates.get(tool.type, 1.0)
    
    def list_tools_with_metadata(self) -> list[dict]:
        """List all registered tools with their metadata.
        
        Useful for debugging and planning.
        """
        tools = []
        for name, metadata in self._tool_metadata.items():
            tools.append({
                "name": name,
                "mode": metadata.execution_mode.value,
                "estimated_seconds": metadata.estimated_duration_seconds,
                "need_auth": metadata.tool.need_auth,
                "type": metadata.tool.type.value,
            })
        return tools


