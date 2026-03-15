"""Message and communication types."""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""
    tool_id: str
    name: str
    arguments: dict[str, Any]
    
    def to_dict(self) -> dict:
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ToolResult:
    """Result from a tool execution."""
    tool_call_id: str
    status: str  # "success", "error", "timeout", "auth_required", "blocked"
    result: Any
    error: Optional[str] = None
    execution_time_ms: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)  # For auth_required and blocked status
    
    def to_dict(self) -> dict:
        return {
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "metadata": self.metadata,
        }


@dataclass
class Message:
    """Base message class for agent communication."""
    id: str
    channel_type: str
    sender_id: str
    sender_name: str
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_type": self.channel_type,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class AuthorizationRequest:
    """Request for user authorization (HITL)."""
    id: str
    operation_type: str  # "file_write", "shell_execute", etc.
    tool_name: str
    description: str
    diff_preview: Optional[str] = None  # For file operations
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "operation_type": self.operation_type,
            "tool_name": self.tool_name,
            "description": self.description,
            "diff_preview": self.diff_preview,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class AuthorizationResponse:
    """User response to authorization request."""
    auth_request_id: str
    user_id: str
    approved: bool
    timestamp: datetime = field(default_factory=datetime.utcnow)
    comment: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "auth_request_id": self.auth_request_id,
            "user_id": self.user_id,
            "approved": self.approved,
            "timestamp": self.timestamp.isoformat(),
            "comment": self.comment,
        }
