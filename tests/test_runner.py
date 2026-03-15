"""Tests for multi-channel runner (Phase 3).

Tests:
- Channel creation from config
- Agent creation with middleware chain
- Runner integration flow
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from closeclaw.config import ChannelConfig, CloseCrawlConfig, LLMConfig, SafetyConfig
from closeclaw.runner import create_channel, create_agent, _PlaceholderLLM
from closeclaw.channels.cli_channel import CLIChannel
from closeclaw.types import ChannelType


# ====== Channel Creation ======

class TestCreateChannel:
    """Test create_channel factory function."""
    
    def _make_config(self, **overrides):
        """Helper to create a minimal CloseCrawlConfig."""
        defaults = {
            "agent_id": "test-agent",
            "workspace_root": ".",
            "llm": LLMConfig(provider="test", model="test-model"),
            "safety": SafetyConfig(admin_user_ids=["admin1"]),
        }
        defaults.update(overrides)
        return CloseCrawlConfig(**defaults)
    
    def test_create_cli_channel(self):
        """Create CLI channel from config."""
        ch_config = ChannelConfig(type="cli", enabled=True)
        config = self._make_config()
        
        channel = create_channel(ch_config, config)
        
        assert isinstance(channel, CLIChannel)
        assert channel.channel_type == ChannelType.CLI
    
    def test_create_cli_channel_with_admin(self):
        """CLI channel uses first admin_user_id."""
        ch_config = ChannelConfig(type="cli", enabled=True)
        config = self._make_config(
            safety=SafetyConfig(admin_user_ids=["my_admin_id"])
        )
        
        channel = create_channel(ch_config, config)
        assert channel.user_id == "my_admin_id"
    
    def test_create_unknown_channel_raises(self):
        """Unknown channel type raises ValueError."""
        ch_config = ChannelConfig(type="discord", enabled=True)
        config = self._make_config()
        
        with pytest.raises(ValueError, match="Unknown channel type"):
            create_channel(ch_config, config)
    
    def test_create_telegram_without_token_raises(self):
        """Telegram channel without token raises ValueError."""
        ch_config = ChannelConfig(type="telegram", enabled=True, token=None)
        config = self._make_config()
        
        # This should raise because token is required
        # (may also raise ImportError if python-telegram-bot not installed)
        with pytest.raises((ValueError, ImportError)):
            create_channel(ch_config, config)


# ====== Agent Creation ======

class TestCreateAgent:
    """Test create_agent factory function."""
    
    def _make_config(self, **overrides):
        defaults = {
            "agent_id": "test-agent",
            "workspace_root": ".",
            "llm": LLMConfig(provider="test", model="test-model"),
            "safety": SafetyConfig(
                admin_user_ids=["admin1"],
                command_blacklist_enabled=True,
            ),
        }
        defaults.update(overrides)
        return CloseCrawlConfig(**defaults)
    
    def test_create_agent_basic(self):
        """Create agent with default settings."""
        config = self._make_config()
        agent = create_agent(config)
        
        assert agent.agent_id == "test-agent"
        assert agent.workspace_root == "."
        assert agent.admin_user_id == "admin1"
    
    def test_create_agent_has_middleware(self):
        """Created agent has middleware chain configured."""
        config = self._make_config()
        agent = create_agent(config)
        
        assert agent.middleware_chain is not None
    
    def test_create_agent_has_task_manager(self):
        """Created agent has TaskManager configured."""
        config = self._make_config()
        agent = create_agent(config)
        
        assert hasattr(agent, "task_manager")
        assert agent.task_manager is not None
    
    def test_create_agent_has_tools(self):
        """Created agent has tools registered."""
        config = self._make_config()
        agent = create_agent(config)
        
        # Should have tools from file_tools, shell_tools, web_tools
        assert len(agent.tools) > 0


# ====== Placeholder LLM ======

class TestPlaceholderLLM:
    """Test placeholder LLM for development."""
    
    async def test_placeholder_llm_echoes(self):
        """Placeholder LLM echoes last message."""
        llm = _PlaceholderLLM()
        
        messages = [{"role": "user", "content": "Hello world"}]
        response, tool_calls = await llm.generate(messages, tools=[])
        
        assert "Hello world" in response
        assert tool_calls is None
    
    async def test_placeholder_llm_empty_messages(self):
        """Placeholder LLM handles empty messages."""
        llm = _PlaceholderLLM()
        
        response, tool_calls = await llm.generate([], tools=[])
        
        assert "Hello" in response
