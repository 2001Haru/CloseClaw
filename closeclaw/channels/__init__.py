"""Channels module - Communication interfaces.

Phase 3: Telegram, Feishu, CLI channel implementations.
"""

from .base import BaseChannel
from .cli_channel import CLIChannel

# Lazy imports for optional dependencies
def get_telegram_channel():
    """Get TelegramChannel class (requires python-telegram-bot)."""
    from .telegram import TelegramChannel
    return TelegramChannel

def get_feishu_channel():
    """Get FeishuChannel class (requires httpx)."""
    from .feishu import FeishuChannel
    return FeishuChannel

def get_discord_channel():
    """Get DiscordChannel class (requires discord.py)."""
    from .discord import DiscordChannel
    return DiscordChannel

def get_whatsapp_channel():
    """Get WhatsAppChannel class (requires websockets bridge)."""
    from .whatsapp import WhatsAppChannel
    return WhatsAppChannel

def get_qq_channel():
    """Get QQChannel class (requires qq-botpy)."""
    from .qq import QQChannel
    return QQChannel

__all__ = [
    "BaseChannel",
    "CLIChannel",
    "get_telegram_channel",
    "get_feishu_channel",
    "get_discord_channel",
    "get_whatsapp_channel",
    "get_qq_channel",
]

