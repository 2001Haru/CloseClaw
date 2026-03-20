"""CloseClaw - A lightweight and safe Python Agent framework."""

__version__ = "0.1.0"

from .agents import AgentCore, LLMProvider
from .config import CloseCrawlConfig, ConfigLoader
from .types import (
    AgentState, OperationType, ChannelType, ToolType,
    Message, ToolCall, ToolResult,
    AuthorizationRequest, AuthorizationResponse,
    Tool, Agent, Session, AgentConfig,
)
from .middleware import MiddlewareChain, SafetyGuard, PathSandbox, AuthPermissionMiddleware
from .tools import get_registered_tools, get_tool_by_name
from .safety import AuditLogger

__all__ = [
    "__version__",
    # Agents
    "AgentCore",
    "LLMProvider",
    # Config
    "CloseCrawlConfig",
    "ConfigLoader",
    # Types - Enums
    "AgentState",
    "OperationType",
    "ChannelType",
    "ToolType",
    # Types - Messages
    "Message",
    "ToolCall",
    "ToolResult",
    "AuthorizationRequest",
    "AuthorizationResponse",
    # Types - Models
    "Tool",
    "Agent",
    "Session",
    "AgentConfig",
    # Middleware
    "MiddlewareChain",
    "SafetyGuard",
    "PathSandbox",
    "AuthPermissionMiddleware",
    # Tools
    "get_registered_tools",
    "get_tool_by_name",
    # Safety
    "AuditLogger",
]

