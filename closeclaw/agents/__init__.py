"""Agent module."""

from .core import AgentCore, LLMProvider
from .task_manager import TaskManager
from ..types import AgentConfig

# Alias for backward compatibility
Agent = AgentCore

__all__ = ["Agent", "AgentCore", "LLMProvider", "AgentConfig", "TaskManager"]
