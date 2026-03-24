"""Deprecated compatibility module.

Use closeclaw.providers.openai_compatible and closeclaw.providers.factory instead.
"""

from .factory import create_llm_provider
from .openai_compatible import OpenAICompatibleProvider
from .litellm_provider import LiteLLMProvider

__all__ = ["OpenAICompatibleProvider", "LiteLLMProvider", "create_llm_provider"]
