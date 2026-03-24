"""Enumerations for CloseClaw system types."""

from enum import Enum


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
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    QQ = "qq"


class ToolType(str, Enum):
    """Supported tool types."""
    FILE = "file"
    WEBSEARCH = "websearch"
    SHELL = "shell"


class TaskStatus(str, Enum):
    """Background task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


