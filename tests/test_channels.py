"""Tests for channel system (Phase 3).

Tests:
- BaseChannel interface compliance
- CLIChannel message handling and HITL prompts
- TelegramChannel initialization and message conversion (mocked)
- FeishuChannel initialization and webhook event handling (mocked)
- Channel __init__.py exports
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from closeclaw.types import Message, ChannelType, AuthorizationResponse
from closeclaw.channels.base import BaseChannel
from closeclaw.channels.cli_channel import CLIChannel


# ====== BaseChannel ======

class TestBaseChannel:
    """Test BaseChannel abstract interface."""
    
    def test_base_channel_cannot_be_instantiated(self):
        """BaseChannel is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            BaseChannel(channel_type=ChannelType.CLI)
    
    def test_base_channel_subclass_requirements(self):
        """Subclass must implement all abstract methods."""
        # Verify CLIChannel is a valid subclass
        channel = CLIChannel()
        assert isinstance(channel, BaseChannel)
        assert channel.channel_type == ChannelType.CLI
    
    def test_create_message_helper(self):
        """Test _create_message helper creates proper Message objects."""
        channel = CLIChannel()
        msg = channel._create_message(
            message_id="test_1",
            sender_id="user123",
            sender_name="Test User",
            content="Hello",
            extra="metadata",
        )
        assert isinstance(msg, Message)
        assert msg.id == "test_1"
        assert msg.sender_id == "user123"
        assert msg.sender_name == "Test User"
        assert msg.content == "Hello"
        assert msg.channel_type == "cli"
        assert msg.metadata.get("extra") == "metadata"


# ====== CLIChannel ======

class TestCLIChannel:
    """Test CLI channel implementation."""
    
    def test_cli_channel_creation(self):
        """CLIChannel initializes with correct defaults."""
        channel = CLIChannel()
        assert channel.channel_type == ChannelType.CLI
        assert channel.user_id == "cli_user"
        assert channel.user_name == "Local User"
        assert not channel.is_running
    
    def test_cli_channel_custom_user(self):
        """CLIChannel accepts custom user info."""
        channel = CLIChannel(user_id="admin_1", user_name="Admin")
        assert channel.user_id == "admin_1"
        assert channel.user_name == "Admin"
    
    async def test_cli_channel_start_stop(self):
        """CLIChannel start/stop lifecycle."""
        channel = CLIChannel()
        
        await channel.start()
        assert channel.is_running
        
        await channel.stop()
        assert not channel.is_running
    
    async def test_cli_channel_receive_message(self):
        """CLIChannel converts stdin to Message objects."""
        channel = CLIChannel()
        await channel.start()
        
        # Mock input to return a test message
        with patch("builtins.input", return_value="Hello Agent"):
            msg = await channel.receive_message()
        
        assert msg is not None
        assert msg.content == "Hello Agent"
        assert msg.sender_id == "cli_user"
        assert msg.channel_type == "cli"
        
        await channel.stop()
    
    async def test_cli_channel_exit_command(self):
        """CLIChannel returns None on exit commands."""
        channel = CLIChannel()
        await channel.start()
        
        with patch("builtins.input", return_value="exit"):
            msg = await channel.receive_message()
        
        assert msg is None
        await channel.stop()
    
    async def test_cli_channel_quit_command(self):
        """CLIChannel returns None on /quit."""
        channel = CLIChannel()
        await channel.start()
        
        with patch("builtins.input", return_value="/quit"):
            msg = await channel.receive_message()
        
        assert msg is None
        await channel.stop()
    
    async def test_cli_channel_send_response(self, capsys):
        """CLIChannel prints response to stdout."""
        channel = CLIChannel()
        await channel.start()
        
        await channel.send_response({
            "type": "response",
            "response": "Hello human!",
        })
        
        captured = capsys.readouterr()
        assert "Hello human!" in captured.out
        
        await channel.stop()
    
    async def test_cli_channel_send_error(self, capsys):
        """CLIChannel prints errors."""
        channel = CLIChannel()
        await channel.start()
        
        await channel.send_response({
            "type": "error",
            "error": "Something failed",
        })
        
        captured = capsys.readouterr()
        assert "Something failed" in captured.out
        
        await channel.stop()
    
    async def test_cli_channel_send_task_completed(self, capsys):
        """CLIChannel prints task completion notifications."""
        channel = CLIChannel()
        await channel.start()
        
        await channel.send_response({
            "type": "task_completed",
            "task_id": "#001",
            "status": "completed",
            "result": "Done!",
        })
        
        captured = capsys.readouterr()
        assert "#001" in captured.out
        assert "completed" in captured.out
        
        await channel.stop()

    async def test_cli_auth_request_does_not_prompt_inline(self):
        """CLI auth_request rendering should not read approval input directly."""
        channel = CLIChannel()
        await channel.start()

        with patch.object(channel, "wait_for_auth_response", new_callable=AsyncMock) as wait_mock:
            await channel.send_response({
                "type": "auth_request",
                "auth_request_id": "auth_123",
                "tool_name": "write_file",
                "description": "write_file requires authorization",
                "diff_preview": None,
            })

        wait_mock.assert_not_awaited()
        await channel.stop()
    
    async def test_cli_channel_auth_approve(self):
        """CLIChannel HITL confirmation - approve."""
        channel = CLIChannel()
        await channel.start()
        
        # Mock input for approval
        with patch("builtins.input", return_value="y"):
            response = await channel.wait_for_auth_response("auth_123")
        
        assert response is not None
        assert response.approved is True
        assert response.auth_request_id == "auth_123"
        
        await channel.stop()
    
    async def test_cli_channel_auth_reject(self):
        """CLIChannel HITL confirmation - reject."""
        channel = CLIChannel()
        await channel.start()
        
        with patch("builtins.input", return_value="n"):
            response = await channel.wait_for_auth_response("auth_456")
        
        assert response is not None
        assert response.approved is False
        
        await channel.stop()
    
    async def test_cli_channel_auth_empty_defaults_yes(self):
        """CLIChannel HITL: empty input defaults to yes."""
        channel = CLIChannel()
        await channel.start()
        
        with patch("builtins.input", return_value=""):
            response = await channel.wait_for_auth_response("auth_789")
        
        assert response is not None
        assert response.approved is True
        
        await channel.stop()
    
    async def test_cli_channel_message_counter(self):
        """CLIChannel increments message counter."""
        channel = CLIChannel()
        await channel.start()
        
        with patch("builtins.input", return_value="msg 1"):
            msg1 = await channel.receive_message()
        with patch("builtins.input", return_value="msg 2"):
            msg2 = await channel.receive_message()
        
        assert "1" in msg1.id
        assert "2" in msg2.id
        
        await channel.stop()

    async def test_cli_prompt_waits_for_agent_response(self):
        """CLI should not prompt the next input before sending current turn response."""
        channel = CLIChannel()
        calls = {"count": 0}

        def _fake_input(_prompt: str = ""):
            calls["count"] += 1
            if calls["count"] == 1:
                return "first"
            return "second"

        with patch("builtins.input", side_effect=_fake_input):
            await channel.start()
            msg = await channel.receive_message()
            assert msg is not None
            assert msg.content == "first"

            # Give stdin loop a chance; it should still be blocked by input gate.
            await asyncio.sleep(0.05)
            assert calls["count"] == 1

            await channel.send_response({"type": "response", "response": "ok"})

            # Now prompt is allowed for the next turn.
            await asyncio.sleep(0.05)
            assert calls["count"] >= 2

            await channel.stop()


# ====== TelegramChannel ======

class TestTelegramChannel:
    """Test Telegram channel (mocked - no actual Telegram connection)."""
    
    def test_telegram_import_check(self):
        """Verify telegram channel can be imported."""
        from closeclaw.channels.telegram import TelegramChannel, HAS_TELEGRAM
        # If python-telegram-bot isn't installed, HAS_TELEGRAM is False
        # The class should still be importable
        assert TelegramChannel is not None
    
    def test_telegram_requires_lib(self):
        """TelegramChannel raises ImportError if lib not installed."""
        from closeclaw.channels.telegram import HAS_TELEGRAM
        if not HAS_TELEGRAM:
            from closeclaw.channels.telegram import TelegramChannel
            with pytest.raises(ImportError, match="python-telegram-bot"):
                TelegramChannel(token="test_token")


# ====== FeishuChannel ======

class TestFeishuChannel:
    """Test Feishu channel (mocked - no actual Feishu connection)."""
    
    def test_feishu_channel_creation(self):
        """FeishuChannel initializes correctly."""
        from closeclaw.channels.feishu import FeishuChannel
        
        channel = FeishuChannel(
            app_id="test_app_id",
            app_secret="test_secret",
            admin_user_ids=["admin1"],
        )
        
        assert channel.channel_type == ChannelType.FEISHU
        assert channel.app_id == "test_app_id"
        assert "admin1" in channel.admin_user_ids
    
    async def test_feishu_webhook_challenge(self):
        """FeishuChannel handles URL verification challenge."""
        from closeclaw.channels.feishu import FeishuChannel
        
        channel = FeishuChannel(
            app_id="test",
            app_secret="test",
        )
        
        # Simulate challenge request
        import json
        challenge_body = json.dumps({"challenge": "abc123"}).encode()
        result = await channel._process_webhook_event(challenge_body)
        
        parsed = json.loads(result)
        assert parsed["challenge"] == "abc123"
    
    async def test_feishu_message_event(self):
        """FeishuChannel processes incoming message events."""
        from closeclaw.channels.feishu import FeishuChannel
        
        channel = FeishuChannel(
            app_id="test",
            app_secret="test",
        )
        
        import json
        event_body = json.dumps({
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_id": "msg_001",
                    "message_type": "text",
                    "content": json.dumps({"text": "Hello from Feishu"}),
                    "chat_id": "chat_123",
                },
                "sender": {
                    "sender_id": {"open_id": "user_001"},
                },
            },
        }).encode()
        
        await channel._process_webhook_event(event_body)
        
        # Message should be in queue
        msg = await asyncio.wait_for(channel._message_queue.get(), timeout=1.0)
        assert msg.content == "Hello from Feishu"
        assert msg.sender_id == "user_001"
    
    async def test_feishu_card_action(self):
        """FeishuChannel processes card action callback (auth response)."""
        from closeclaw.channels.feishu import FeishuChannel
        
        channel = FeishuChannel(
            app_id="test",
            app_secret="test",
            admin_user_ids=["admin_user"],
        )
        
        # Create a pending future
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        channel._auth_futures["auth_001"] = future
        
        import json
        event_body = json.dumps({
            "header": {"event_type": "card.action.trigger"},
            "event": {
                "action": {
                    "value": {"action": "approve", "auth_request_id": "auth_001"},
                },
                "operator": {"open_id": "admin_user"},
            },
        }).encode()
        
        await channel._process_webhook_event(event_body)
        
        # Future should be resolved
        assert future.done()
        result = future.result()
        assert result.approved is True
        assert result.auth_request_id == "auth_001"


# ====== Channel __init__ ======

class TestChannelExports:
    """Test channel module exports."""
    
    def test_base_channel_exported(self):
        from closeclaw.channels import BaseChannel
        assert BaseChannel is not None
    
    def test_cli_channel_exported(self):
        from closeclaw.channels import CLIChannel
        assert CLIChannel is not None
    
    def test_telegram_getter(self):
        from closeclaw.channels import get_telegram_channel
        assert callable(get_telegram_channel)
    
    def test_feishu_getter(self):
        from closeclaw.channels import get_feishu_channel
        assert callable(get_feishu_channel)

    def test_discord_getter(self):
        from closeclaw.channels import get_discord_channel
        assert callable(get_discord_channel)

    def test_whatsapp_getter(self):
        from closeclaw.channels import get_whatsapp_channel
        assert callable(get_whatsapp_channel)

    def test_qq_getter(self):
        from closeclaw.channels import get_qq_channel
        assert callable(get_qq_channel)


class TestNewChannelImports:
    """Smoke tests for newly added channel modules."""

    def test_discord_module_import(self):
        from closeclaw.channels.discord import DiscordChannel, HAS_DISCORD
        assert DiscordChannel is not None
        assert isinstance(HAS_DISCORD, bool)

    def test_whatsapp_module_import(self):
        from closeclaw.channels.whatsapp import WhatsAppChannel, HAS_WHATSAPP_BRIDGE
        assert WhatsAppChannel is not None
        assert isinstance(HAS_WHATSAPP_BRIDGE, bool)

    def test_qq_module_import(self):
        from closeclaw.channels.qq import QQChannel, HAS_QQ
        assert QQChannel is not None
        assert isinstance(HAS_QQ, bool)


def test_channel_type_enum_contains_phase_d_channels():
    assert ChannelType.DISCORD.value == "discord"
    assert ChannelType.WHATSAPP.value == "whatsapp"
    assert ChannelType.QQ.value == "qq"





