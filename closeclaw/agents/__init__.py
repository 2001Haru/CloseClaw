"""Agent module."""

from .core import AgentCore, LLMProvider
from .task_manager import TaskManager
from .llm_providers import OpenAICompatibleProvider, create_llm_provider
from ..types import AgentConfig

# Alias for backward compatibility
Agent = AgentCore

__all__ = [
    "Agent", "AgentCore", "LLMProvider", "AgentConfig", "TaskManager",
    "OpenAICompatibleProvider", "create_llm_provider",
]

