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

__all__ = [
    "BaseChannel",
    "CLIChannel",
    "get_telegram_channel",
    "get_feishu_channel",
]
