"""Tool system."""

from .base import (
    tool, BaseTool, ToolRegistry, 
    get_registered_tools, get_tool_by_name
)
from .file_tools import (
    read_file_impl, write_memory_file_impl, edit_memory_file_impl, write_file_impl, edit_file_impl,
    delete_file_impl, delete_lines_impl, list_files_impl, file_exists_impl,
    get_file_size_impl
)
from .cron_tools import call_cron_impl
from .shell_tools import shell_impl, pwd_impl
from .spawn_tools import spawn_impl, task_status_impl, task_cancel_impl
from .web_tools import web_search_impl, configure_web_search
from .document_tools import read_pdf_impl, read_image_impl
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
    "write_memory_file_impl",
    "edit_memory_file_impl",
    "write_file_impl",
    "edit_file_impl",
    "delete_file_impl",
    "delete_lines_impl",
    "list_files_impl",
    "file_exists_impl",
    "get_file_size_impl",
    # Cron tools
    "call_cron_impl",
    # Shell tools
    "shell_impl",
    "pwd_impl",
    # Spawn tools
    "spawn_impl",
    "task_status_impl",
    "task_cancel_impl",
    # Web tools
    "configure_web_search",
    "web_search_impl",
    # Document tools
    "read_pdf_impl",
    "read_image_impl",
]

