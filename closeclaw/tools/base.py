"""Tool system base classes and decorators."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional
import inspect

from ..types import Tool, ToolType

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for all tools in the system."""
    
    def __init__(self):
        self.tools: dict[str, Tool] = {}
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self.tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name} (need_auth={tool.need_auth})")
    
    def get(self, name: str) -> Optional[Tool]:
        """Get tool by name."""
        return self.tools.get(name)
    
    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self.tools.values())


# Global registry
_tool_registry = ToolRegistry()


def tool(name: str = None,
         description: str = None,
         need_auth: bool = False,
         tool_type: ToolType = ToolType.SHELL,
         parameters: dict[str, Any] = None):
    """Decorator to register a tool function as an agent tool.
    
    Usage:
        @tool(
            name="read_file",
            description="Read file content",
            need_auth=False,
            tool_type=ToolType.FILE,
            parameters={
                "path": {"type": "string", "description": "File path"}
            }
        )
        async def read_file_impl(path: str) -> str:
            ...
    """
    
    def decorator(func: Callable) -> Callable:
        # Infer name from function if not provided
        tool_name = name or func.__name__
        tool_desc = description or func.__doc__ or tool_name
        
        # Create tool object
        tool_obj = Tool(
            name=tool_name,
            description=tool_desc,
            type=tool_type,
            need_auth=need_auth,
            handler=func,
            parameters=parameters or {},
        )
        
        # Register in global registry
        _tool_registry.register(tool_obj)
        
        return func
    
    return decorator


class BaseTool(ABC):
    """Base class for tool implementations."""
    
    name: str
    description: str
    need_auth: bool = False
    
    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Execute the tool with given arguments."""
        ...
    
    @abstractmethod
    def get_parameters(self) -> dict[str, Any]:
        """Get parameter schema for LLM function calling."""
        ...
    
    def to_tool(self) -> Tool:
        """Convert to Tool object."""
        return Tool(
            name=self.name,
            description=self.description,
            type=ToolType.SHELL,  # Override as needed in subclass
            need_auth=self.need_auth,
            handler=self.execute,
            parameters=self.get_parameters(),
        )


def get_registered_tools() -> list[Tool]:
    """Get all registered tools."""
    return _tool_registry.list_tools()


def get_tool_by_name(name: str) -> Optional[Tool]:
    """Get tool by name from registry."""
    return _tool_registry.get(name)


