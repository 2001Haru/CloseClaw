"""Enumerations for CloseClaw system types."""

from enum import Enum


class Zone(str, Enum):
    """Trust zones for tool execution.
    
    - ZONE_A: Safe operations, auto-execute (e.g., read-only file ops, simple web search)
    - ZONE_B: Internal operations, silent+log (e.g., logs, metadata updates)
    - ZONE_C: Dangerous operations, require HITL confirmation (e.g., file writes, deletions, shell)
    """
    ZONE_A = "A"
    ZONE_B = "B"
    ZONE_C = "C"


class AgentState(str, Enum):
    """Agent execution states."""
    IDLE = "idle"
    RUNNING = "running"
    WAITING_FOR_AUTH = "waiting_for_auth"  # Blocked waiting for user confirmation
    ERROR = "error"
    PAUSED = "paused"


class OperationType(str, Enum):
    """Types of file operations."""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    CREATE = "create"
    MODIFY = "modify"
    EXECUTE = "execute"


class ChannelType(str, Enum):
    """Supported communication channels."""
    TELEGRAM = "telegram"
    FEISHU = "feishu"
    CLI = "cli"


class ToolType(str, Enum):
    """Supported tool types."""
    FILE = "file"
    WEBSEARCH = "websearch"
    SHELL = "shell"
