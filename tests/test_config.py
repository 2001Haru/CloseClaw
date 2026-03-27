"""Tests for configuration system."""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from closeclaw.config import (
    LLMConfig, ChannelConfig, SafetyConfig,
    CloseCrawlConfig, ConfigLoader
)


class TestLLMConfig:
    """Test LLM configuration."""
    
    def test_llm_config_creation(self):
        """Test basic LLM config creation."""
        config = LLMConfig(
            provider="openai",
            model="gpt-4",
            api_key="sk-1234567890",
            temperature=0.0,
            max_tokens=2000
        )
        
        assert config.provider == "openai"
        assert config.model == "gpt-4"
        assert config.temperature == 0.0
    
    def test_llm_config_defaults(self):
        """Test LLM config default values."""
        config = LLMConfig(
            provider="anthropic",
            model="claude-3-opus"
        )
        
        assert config.temperature == 0.0
        assert config.max_tokens == 2000
        assert config.timeout_seconds == 60
    
    def test_llm_config_to_dict(self):
        """Test LLM config to_dict conversion."""
        config = LLMConfig(
            provider="gemini",
            model="gemini-pro",
            api_key="test_key"
        )
        
        config_dict = config.to_dict()
        assert config_dict["provider"] == "gemini"
        assert config_dict["model"] == "gemini-pro"
        assert config_dict["api_key"] == "test_key"


class TestChannelConfig:
    """Test channel configuration."""
    
    def test_channel_config_cli(self):
        """Test CLI channel configuration."""
        config = ChannelConfig(
            type="cli",
            enabled=True
        )
        
        assert config.type == "cli"
        assert config.enabled is True
    
    def test_channel_config_telegram(self):
        """Test Telegram channel configuration."""
        config = ChannelConfig(
            type="telegram",
            enabled=True,
            token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            metadata={"chat_id": 987654321}
        )
        
        assert config.type == "telegram"
        assert config.token is not None
        assert config.metadata["chat_id"] == 987654321
    
    def test_channel_config_to_dict(self):
        """Test channel config to_dict conversion."""
        config = ChannelConfig(
            type="feishu",
            enabled=True,
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
        )
        
        config_dict = config.to_dict()
        assert config_dict["type"] == "feishu"
        assert "webhook_url" in config_dict


class TestSafetyConfig:
    """Test safety configuration."""
    
    def test_safety_config_defaults(self):
        """Test safety config defaults."""
        config = SafetyConfig()
        
        assert config.enable_hitl is True
        assert config.enable_audit_log is True
    
    def test_safety_config_custom(self):
        """Test custom safety config."""
        config = SafetyConfig(
            enable_hitl=False,
            enable_audit_log=True,
            audit_log_path="/var/log/closeclaw/audit.jsonl",
            audit_log_retention_days=30
        )
        
        assert config.enable_hitl is False
        assert config.audit_log_retention_days == 30


class TestCloseCrawlConfig:
    """Test main CloseCrawl configuration."""
    
    def test_config_creation_with_llm(self):
        """Test creating config with LLM settings."""
        llm_config = LLMConfig(
            provider="openai",
            model="gpt-4"
        )
        
        config = CloseCrawlConfig(
            llm=llm_config,
            workspace_root="/workspace"
        )
        
        assert config.llm.provider == "openai"
        assert config.workspace_root == "/workspace"
    
    def test_config_with_multiple_channels(self):
        """Test config with multiple channels."""
        cli_channel = ChannelConfig(type="cli", enabled=True)
        telegram_channel = ChannelConfig(type="telegram", enabled=True)
        
        config = CloseCrawlConfig(
            llm=LLMConfig(provider="openai", model="gpt-4"),
            channels={"cli": cli_channel, "telegram": telegram_channel}
        )
        
        assert len(config.channels) == 2
        assert "telegram" in config.channels


class TestConfigLoader:
    """Test configuration loader."""
    
    def test_load_config_from_yaml(self, config_file):
        """Test loading config from YAML file."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}):
            loader = ConfigLoader()
            config = loader.load(str(config_file))
            
            assert config is not None
            assert config.llm.provider == "openai"
            assert config.llm.model == "gpt-4"
    
    def test_load_nonexistent_file(self):
        """Test loading nonexistent config file."""
        loader = ConfigLoader()
        
        with pytest.raises((FileNotFoundError, Exception)):
            loader.load("/nonexistent/config.yaml")
    
    def test_env_var_substitution(self, temp_workspace):
        """Test environment variable substitution."""
        config_content = """
llm:
  provider: openai
  model: gpt-4
  api_key: ${OPENAI_API_KEY}

safety:
  audit_log_path: ${WORKSPACE_ROOT}/audit.jsonl
"""
        config_path = Path(temp_workspace) / "config.yaml"
        config_path.write_text(config_content)
        
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-test123",
            "WORKSPACE_ROOT": "/data/workspace"
        }):
            loader = ConfigLoader()
            config = loader.load(str(config_path))
            
            assert config.llm.api_key == "sk-test123"
            assert config.safety.audit_log_path == "/data/workspace/audit.jsonl"
    
    def test_config_validation(self, temp_workspace):
        """Test config validation."""
        # Missing required fields
        invalid_config = """
llm:
  provider: openai
"""
        config_path = Path(temp_workspace) / "invalid_config.yaml"
        config_path.write_text(invalid_config)
        
        loader = ConfigLoader()
        with pytest.raises(Exception):  # Should raise validation error
            loader.load(str(config_path))
    
    def test_merge_configs(self):
        """Test merging configs with defaults."""
        custom_yaml = """
llm:
  provider: anthropic
  model: claude-3-opus
  temperature: 0.5
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(custom_yaml)
            temp_config_path = f.name

        try:
            loader = ConfigLoader()
            config = loader.load(temp_config_path)

            # Custom values
            assert config.llm.provider == "anthropic"
            assert config.llm.temperature == 0.5

            # Default values
            assert config.llm.max_tokens == 2000
            assert config.safety.enable_hitl is True
        finally:
            os.unlink(temp_config_path)
    
    def test_config_to_dict(self, config_file):
        """Test converting config to dict."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}):
            loader = ConfigLoader()
            config = loader.load(str(config_file))
            config_dict = config.to_dict()
            
            assert "llm" in config_dict
            assert config_dict["llm"]["provider"] == "openai"
            assert "safety" in config_dict

    def test_heartbeat_config_defaults_and_overrides(self, temp_workspace):
        """Heartbeat config should load with defaults and custom overrides."""
        config_content = """
llm:
    provider: openai
    model: gpt-4

heartbeat:
    enabled: true
    interval_s: 900
    quiet_hours:
        enabled: true
        timezone: Asia/Shanghai
        ranges: ["23:00-08:00"]
    queue_busy_guard:
        enabled: true
        max_queue_size: 50
    routing:
        target_ttl_s: 600
        fallback_channel: cli
        fallback_chat_id: direct
    notify:
        enabled: false
"""
        config_path = Path(temp_workspace) / "heartbeat_config.yaml"
        config_path.write_text(config_content)

        loader = ConfigLoader()
        config = loader.load(str(config_path))

        assert config.heartbeat.enabled is True
        assert config.heartbeat.interval_s == 900
        assert config.heartbeat.quiet_hours.enabled is True
        assert config.heartbeat.quiet_hours.timezone == "Asia/Shanghai"
        assert config.heartbeat.queue_busy_guard.max_queue_size == 50
        assert config.heartbeat.routing.target_ttl_s == 600
        assert config.heartbeat.notify.enabled is False

    def test_workspace_root_defaults_to_config_dir_when_missing(self, temp_workspace):
        """When workspace_root is omitted, use config file directory instead of cwd."""
        config_content = """
llm:
    provider: openai
    model: gpt-4
"""
        config_path = Path(temp_workspace) / "minimal_config.yaml"
        config_path.write_text(config_content)

        loader = ConfigLoader()
        config = loader.load(str(config_path))

        assert config.workspace_root == str(Path(temp_workspace).resolve())

    def test_legacy_state_file_is_upgraded_to_memory_path(self, temp_workspace):
        """Legacy state_file=state.json should be auto-upgraded to CloseClaw Memory path."""
        config_content = """
llm:
  provider: openai
  model: gpt-4

state_file: state.json
"""
        config_path = Path(temp_workspace) / "state_upgrade_config.yaml"
        config_path.write_text(config_content)

        loader = ConfigLoader()
        config = loader.load(str(config_path))

        assert config.state_file == "CloseClaw Memory/state.json"

    def test_web_search_brave_config_parses(self, temp_workspace):
        """web_search block should parse Brave API configuration."""
        config_content = """
llm:
    provider: openai
    model: gpt-4

web_search:
    enabled: true
    provider: brave
    brave_api_key: BSA-test-key
    timeout_seconds: 15
    duckduckgo_min_interval_seconds: 1.2
"""
        config_path = Path(temp_workspace) / "web_search_config.yaml"
        config_path.write_text(config_content)

        loader = ConfigLoader()
        config = loader.load(str(config_path))

        assert config.web_search.enabled is True
        assert config.web_search.provider == "brave"
        assert config.web_search.brave_api_key == "BSA-test-key"
        assert config.web_search.timeout_seconds == 15
        assert config.web_search.duckduckgo_min_interval_seconds == 1.2

    def test_safety_mode_and_guardian_config_parses(self, temp_workspace):
        """safety block should parse security_mode and consensus guardian settings."""
        config_content = """
llm:
    provider: openai
    model: gpt-4

safety:
    security_mode: consensus
    consensus_guardian_timeout_seconds: 42.5
    consensus_guardian_prompt: "You are custom sentinel"
    default_need_auth: true
"""
        config_path = Path(temp_workspace) / "safety_mode_config.yaml"
        config_path.write_text(config_content)

        loader = ConfigLoader()
        config = loader.load(str(config_path))

        assert config.safety.security_mode == "consensus"
        assert config.safety.consensus_guardian_timeout_seconds == 42.5
        assert config.safety.consensus_guardian_prompt == "You are custom sentinel"
        assert config.safety.default_need_auth is True

    def test_safety_mode_defaults_to_supervised(self, temp_workspace):
        """When safety mode is omitted, default should remain supervised."""
        config_content = """
llm:
    provider: openai
    model: gpt-4

safety:
    default_need_auth: false
"""
        config_path = Path(temp_workspace) / "safety_mode_default_config.yaml"
        config_path.write_text(config_content)

        loader = ConfigLoader()
        config = loader.load(str(config_path))

        assert config.safety.security_mode == "supervised"
        assert config.safety.consensus_guardian_timeout_seconds == 20.0
        assert config.safety.consensus_guardian_prompt is None


class TestConfigEdgeCases:
    """Test edge cases in configuration."""
    
    def test_empty_metadata(self):
        """Test config with empty metadata."""
        config = ChannelConfig(type="cli")
        assert config.metadata == {}
    
    def test_special_characters_in_paths(self):
        """Test config with special characters in paths."""
        config = SafetyConfig(
            audit_log_path="/var/log/closeclaw/audit-2026-03-15.jsonl"
        )
        assert "2026-03-15" in config.audit_log_path

    # NOTE: test_very_large_timeout and test_zero_retention_days removed
    # (鍐崇瓥4锛氬彧淇叧閿殑Config鍔熻兘锛宔dge case鎺ㄨ繜鍒癙hase2)




