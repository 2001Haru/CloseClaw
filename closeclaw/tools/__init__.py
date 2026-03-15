"""Tool system."""

from .base import (
    tool, BaseTool, ToolRegistry, 
    get_registered_tools, get_tool_by_name
)
from .file_tools import (
    read_file_impl, write_file_impl, append_file_impl,
    delete_file_impl, list_files_impl, file_exists_impl,
    get_file_size_impl
)
from .shell_tools import shell_impl, pwd_impl
from .web_tools import web_search_impl
from .adaptation import ToolAdaptationLayer, ExecutionMode, ToolMetadata
from ..types import Tool, ToolType

__all__ = [
    # Base classes and registry
    "tool",
    "BaseTool",
    "ToolRegistry",
    "get_registered_tools",
    "get_tool_by_name",
    # Types
    "Tool",
    "ToolType",
    # Tool adaptation (Phase 2)
    "ToolAdaptationLayer",
    "ExecutionMode",
    "ToolMetadata",
    # File tools
    "read_file_impl",
    "write_file_impl",
    "append_file_impl",
    "delete_file_impl",
    "list_files_impl",
    "file_exists_impl",
    "get_file_size_impl",
    # Shell tools
    "shell_impl",
    "pwd_impl",
    # Web tools
    "web_search_impl",
]
