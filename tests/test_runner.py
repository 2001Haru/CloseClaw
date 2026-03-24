"""Tests for multi-channel runner (Phase 3).

Tests:
- Channel creation from config
- Agent creation with middleware chain
- Runner integration flow
"""

import pytest
import os
import asyncio
import yaml
from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace
from pathlib import Path

from closeclaw.config import ChannelConfig, CloseCrawlConfig, LLMConfig, SafetyConfig
from closeclaw.runner import (
    create_channel,
    create_agent,
    run_channel,
    _PlaceholderLLM,
    _is_channel_allowed_for_mode,
    _build_gateway_startup_summary,
    _enqueue_cron_wake_message,
    _load_mcp_servers_from_config,
    _bootstrap_mcp_servers,
)
from closeclaw.channels.cli_channel import CLIChannel
from closeclaw.types import ChannelType, Message


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
        ch_config = ChannelConfig(type="unknown-platform", enabled=True)
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


class TestRunModeChannelFilter:
    def test_agent_mode_allows_only_cli(self):
        assert _is_channel_allowed_for_mode("cli", "agent") is True
        assert _is_channel_allowed_for_mode("telegram", "agent") is False
        assert _is_channel_allowed_for_mode("discord", "agent") is False

    def test_gateway_mode_blocks_cli(self):
        assert _is_channel_allowed_for_mode("cli", "gateway") is False
        assert _is_channel_allowed_for_mode("telegram", "gateway") is True
        assert _is_channel_allowed_for_mode("qq", "gateway") is True

    def test_all_mode_allows_all_channels(self):
        assert _is_channel_allowed_for_mode("cli", "all") is True
        assert _is_channel_allowed_for_mode("telegram", "all") is True
        assert _is_channel_allowed_for_mode("discord", "all") is True


class TestGatewayStartupSummary:
    def test_gateway_summary_no_channels(self):
        lines = _build_gateway_startup_summary([])

        assert "[CloseClaw] Gateway mode started." in lines
        assert "[CloseClaw] Enabled channels: (none)" in lines

    def test_gateway_summary_with_feishu_and_whatsapp(self):
        lines = _build_gateway_startup_summary(
            [
                ChannelConfig(
                    type="feishu",
                    enabled=True,
                    metadata={"webhook_host": "127.0.0.1", "webhook_port": 19090},
                ),
                ChannelConfig(
                    type="whatsapp",
                    enabled=True,
                    metadata={"bridge_url": "http://localhost:3000/webhook"},
                ),
            ]
        )

        assert "[CloseClaw] Enabled channels: feishu, whatsapp" in lines
        assert "[CloseClaw] Feishu webhook: http://127.0.0.1:19090" in lines
        assert "[CloseClaw] WhatsApp bridge: http://localhost:3000/webhook" in lines

    def test_gateway_summary_defaults_feishu_host_port(self):
        lines = _build_gateway_startup_summary(
            [
                ChannelConfig(type="feishu", enabled=True),
            ]
        )

        assert "[CloseClaw] Feishu webhook: http://0.0.0.0:9000" in lines


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
        assert agent.workspace_root == os.path.abspath(".")
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


class TestCronWakeInjection:
    @pytest.mark.asyncio
    async def test_enqueue_cron_wake_message_routes_to_target_channel(self):
        queues: dict[str, asyncio.Queue[Message]] = {
            "cli": asyncio.Queue(),
            "telegram": asyncio.Queue(),
        }
        job = SimpleNamespace(id="job1", message="wake now", channel="telegram", to="123")

        result = await _enqueue_cron_wake_message(wake_queues=queues, job=job)

        assert result["queued"] is True
        assert result["channel"] == "telegram"
        msg = await queues["telegram"].get()
        assert msg.content == "wake now"
        assert msg.sender_id == "system"

    @pytest.mark.asyncio
    async def test_enqueue_cron_wake_message_falls_back_to_cli(self):
        queues: dict[str, asyncio.Queue[Message]] = {
            "cli": asyncio.Queue(),
        }
        job = SimpleNamespace(id="job2", message="wake fallback", channel="feishu", to="direct")

        result = await _enqueue_cron_wake_message(wake_queues=queues, job=job)

        assert result["queued"] is True
        assert result["channel"] == "cli"
        msg = await queues["cli"].get()
        assert msg.content == "wake fallback"

    @pytest.mark.asyncio
    async def test_enqueue_cron_wake_message_no_queue(self):
        job = SimpleNamespace(id="job3", message="wake none", channel="cli", to="direct")

        result = await _enqueue_cron_wake_message(wake_queues={}, job=job)
        assert result["queued"] is False

    @pytest.mark.asyncio
    async def test_enqueue_cron_wake_message_omits_chat_id_for_direct_target(self):
        queues: dict[str, asyncio.Queue[Message]] = {
            "telegram": asyncio.Queue(),
        }
        job = SimpleNamespace(id="job4", message="wake direct", channel="telegram", to="direct")

        await _enqueue_cron_wake_message(wake_queues=queues, job=job)
        msg = await queues["telegram"].get()

        assert "_chat_id" not in msg.metadata


class TestRunChannelRouting:
    @pytest.mark.asyncio
    async def test_run_channel_preserves_last_valid_chat_id_on_cron_direct(self):
        class _FakeChannel:
            channel_type = ChannelType.TELEGRAM

            def __init__(self):
                self._running = False
                self._messages = asyncio.Queue()
                self.sent_responses: list[dict] = []

            async def start(self):
                self._running = True
                await self._messages.put(
                    Message(
                        id="u1",
                        channel_type="telegram",
                        sender_id="user1",
                        sender_name="User",
                        content="schedule reminder",
                        metadata={"_chat_id": 12345},
                    )
                )

            async def stop(self):
                self._running = False

            async def receive_message(self):
                return await self._messages.get()

            async def send_response(self, response: dict):
                self.sent_responses.append(dict(response))

            async def wait_for_auth_response(self, auth_request_id: str, timeout: float = 300.0):
                return None

        class _FakeAgent:
            async def run(
                self,
                *,
                session_id,
                user_id,
                channel_type,
                message_input_fn,
                message_output_fn,
                auth_response_fn,
            ):
                _ = (session_id, user_id, channel_type, auth_response_fn)
                first = await message_input_fn()
                assert first.metadata.get("_chat_id") == 12345
                await message_output_fn({"type": "response", "response": "first"})

                second = await message_input_fn()
                assert second.metadata.get("source") == "cron"
                assert second.metadata.get("_chat_id") is None
                await message_output_fn({"type": "response", "response": "cron reminder"})

        channel = _FakeChannel()
        agent = _FakeAgent()
        config = CloseCrawlConfig(
            agent_id="test-agent",
            workspace_root=".",
            llm=LLMConfig(provider="test", model="test-model"),
            safety=SafetyConfig(admin_user_ids=["admin1"]),
        )

        wake_queue: asyncio.Queue[Message] = asyncio.Queue()

        async def _enqueue_cron_later() -> None:
            await asyncio.sleep(0.01)
            await wake_queue.put(
                Message(
                    id="cron1",
                    channel_type="telegram",
                    sender_id="system",
                    sender_name="System",
                    content="wake",
                    metadata={"role": "system", "source": "cron", "_chat_id": None},
                )
            )

        cron_task = asyncio.create_task(_enqueue_cron_later())
        await run_channel(agent=agent, channel=channel, config=config, wake_queue=wake_queue)
        await cron_task

        assert len(channel.sent_responses) == 2
        assert channel.sent_responses[0].get("_chat_id") == 12345
        assert channel.sent_responses[1].get("_chat_id") == 12345


class TestMCPBootstrap:
    def test_load_mcp_servers_from_config(self, tmp_path: Path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump(
                {
                    "llm": {"provider": "test", "model": "test-model"},
                    "mcp": {
                        "servers": [
                            {
                                "id": "s1",
                                "transport": "stdio",
                                "command": "python",
                                "args": ["-m", "demo.server"],
                            },
                            {
                                "id": "s2",
                                "transport": "http",
                                "base_url": "https://example.com",
                            },
                        ]
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        servers = _load_mcp_servers_from_config(str(config_file))
        assert len(servers) == 2
        assert servers[0]["id"] == "s1"
        assert servers[1]["id"] == "s2"

    @pytest.mark.asyncio
    async def test_bootstrap_mcp_servers_syncs_tools(self, tmp_path: Path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump(
                {
                    "llm": {"provider": "test", "model": "test-model"},
                    "mcp": {
                        "servers": [
                            {
                                "id": "local",
                                "transport": "stdio",
                                "command": "python",
                                "args": ["-m", "demo.server"],
                            },
                            {
                                "id": "remote",
                                "transport": "http",
                                "base_url": "https://example.com",
                            },
                        ]
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        class _FakeBridge:
            def __init__(self, pool):
                self.pool = pool

            async def sync_server_tools(self, server_id, tool_execution_service):
                return [f"{server_id}_tool"]

        class _FakePool:
            def register(self, server_id, client=None, factory=None):
                return None

        monkeypatch.setattr("closeclaw.runner.MCPBridge", _FakeBridge)
        monkeypatch.setattr("closeclaw.runner.MCPClientPool", _FakePool)
        monkeypatch.setattr("closeclaw.runner.MCPStdioClient", lambda **kwargs: object())
        monkeypatch.setattr("closeclaw.runner.MCPHttpClient", lambda **kwargs: object())

        agent = SimpleNamespace(tool_execution_service=object())
        names = await _bootstrap_mcp_servers(agent, str(config_file))

        assert names == ["local_tool", "remote_tool"]

    @pytest.mark.asyncio
    async def test_bootstrap_mcp_servers_tolerates_single_server_failure(self, tmp_path: Path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump(
                {
                    "llm": {"provider": "test", "model": "test-model"},
                    "mcp": {
                        "servers": [
                            {
                                "id": "ok",
                                "transport": "stdio",
                                "command": "python",
                                "args": ["-m", "demo.server"],
                            },
                            {
                                "id": "bad",
                                "transport": "stdio",
                                "command": "python",
                                "args": ["-m", "broken.server"],
                            },
                        ]
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        class _FakeBridge:
            def __init__(self, pool):
                self.pool = pool

            async def sync_server_tools(self, server_id, tool_execution_service):
                if server_id == "bad":
                    raise RuntimeError("boom")
                return ["ok_tool"]

        class _FakePool:
            def register(self, server_id, client=None, factory=None):
                return None

        monkeypatch.setattr("closeclaw.runner.MCPBridge", _FakeBridge)
        monkeypatch.setattr("closeclaw.runner.MCPClientPool", _FakePool)
        monkeypatch.setattr("closeclaw.runner.MCPStdioClient", lambda **kwargs: object())

        agent = SimpleNamespace(tool_execution_service=object())
        names = await _bootstrap_mcp_servers(agent, str(config_file))

        assert names == ["ok_tool"]




