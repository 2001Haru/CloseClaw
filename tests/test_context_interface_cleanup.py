"""Regression tests for context-interface cleanup and recall prompt injection."""

from closeclaw.agents.core import AgentCore
from closeclaw.config import ConfigLoader
from closeclaw.types import AgentConfig


class _DummyLLM:
    async def generate(self, messages, tools, **kwargs):
        return "ok", None


def test_build_system_prompt_includes_memory_recall_policy(tmp_path):
    """Agent system prompt should include baseline recall guidance when memory tool exists."""
    config = AgentConfig(
        model="openai/gpt-4",
        system_prompt="You are a precise assistant.",
    )

    agent = AgentCore(
        agent_id="test-agent",
        llm_provider=_DummyLLM(),
        config=config,
        workspace_root=str(tmp_path),
    )

    prompt = agent._build_system_prompt()

    assert "You are a precise assistant." in prompt
    assert "[MEMORY RECALL POLICY]" in prompt
    assert "retrieve_memory" in prompt


def test_build_config_syncs_legacy_max_context_tokens():
    """Legacy max_context_tokens should feed context_management.max_tokens when not explicitly set."""
    raw_config = {
        "agent_id": "agent-1",
        "workspace_root": ".",
        "llm": {"provider": "openai", "model": "gpt-4"},
        "max_context_tokens": 54321,
        "context_management": {
            "warning_threshold": 0.7,
            "critical_threshold": 0.9,
        },
    }

    config = ConfigLoader._build_config(raw_config)

    assert config.context_management.max_tokens == 54321
    assert config.max_context_tokens == 54321


def test_build_config_uses_context_management_max_tokens_as_source_of_truth():
    """When both fields exist and conflict, context_management.max_tokens should win."""
    raw_config = {
        "agent_id": "agent-2",
        "workspace_root": ".",
        "llm": {"provider": "openai", "model": "gpt-4"},
        "max_context_tokens": 11111,
        "context_management": {
            "max_tokens": 22222,
            "warning_threshold": 0.75,
            "critical_threshold": 0.95,
        },
    }

    config = ConfigLoader._build_config(raw_config)

    assert config.context_management.max_tokens == 22222
    assert config.max_context_tokens == 22222
