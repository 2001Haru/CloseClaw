"""Agent module."""

from .core import AgentCore, LLMProvider
from ..types import AgentConfig

# Alias for backward compatibility
Agent = AgentCore

__all__ = ["Agent", "AgentCore", "LLMProvider", "AgentConfig"]
