"""High-level domain models."""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol
from datetime import datetime
from .enums import Zone, AgentState, ToolType


class ToolProtocol(Protocol):
    """Protocol for tool implementation."""
    name: str
    description: str
    zone: Zone
    
    async def execute(self, **kwargs: Any) -> Any:
        """Execute the tool with given arguments."""
        ...


@dataclass
class Tool:
    """Tool definition with metadata."""
    name: str
    description: str
    zone: Zone
    type: ToolType  # Tool type for middleware filtering (SHELL, FILE, WEBSEARCH, etc.)
    handler: Optional[Callable] = None
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "zone": self.zone.value,
            "type": self.type.value,
            "parameters": self.parameters,
            "metadata": self.metadata,
        }


@dataclass
class Session:
    """Agent session/conversation state."""
    session_id: str
    user_id: str
    channel_type: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    messages: list = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "channel_type": self.channel_type,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "state": self.state,
            "metadata": self.metadata,
        }


@dataclass
class AgentConfig:
    """Agent configuration."""
    model: str  # e.g. "openai/gpt-4", "anthropic/claude-3"
    max_iterations: int = 10
    timeout_seconds: int = 300
    temperature: float = 0.0
    system_prompt: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "max_iterations": self.max_iterations,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "system_prompt": self.system_prompt,
            "metadata": self.metadata,
        }


@dataclass
class Agent:
    """Agent instance."""
    agent_id: str
    config: AgentConfig
    state: AgentState = AgentState.IDLE
    tools: list[Tool] = field(default_factory=list)
    current_session: Optional[Session] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "state": self.state.value,
            "config": self.config.to_dict(),
            "tools": [t.to_dict() for t in self.tools],
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }
