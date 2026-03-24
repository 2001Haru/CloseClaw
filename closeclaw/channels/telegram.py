from __future__ import annotations

"""Telegram channel - Integration with Telegram Bot API.

Uses python-telegram-bot v20+ (async-native).
Implements long polling for message reception and Inline Keyboard
for HITL (Human-in-the-Loop) sensitive operation confirmations.

From Planning.md:
    "Retain Telegram (international standard)"
    "Agent stays in WAITING_FOR_AUTH until approved by a specific user ID"
"""

import asyncio
import logging
import re
from typing import Any, Optional

from .base import BaseChannel
from ..types import Message, ChannelType, AuthorizationResponse

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_CHUNK_SIZE = 3500

# Lazy import to avoid hard dependency
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, 
        MessageHandler, 
        CallbackQueryHandler, 
        ContextTypes,
        filters,
    )
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False


class TelegramChannel(BaseChannel):
    """Telegram Bot channel via python-telegram-bot v20+.
    
    Features:
    - Long polling message reception
    - Inline Keyboard for HITL confirmation (need_auth tools)
    - Admin user ID verification for auth approvals
    - Background task completion notifications
    - Structured Diff preview in messages
    
    Requirements:
        pip install closeclaw[telegram]
        # or: pip install python-telegram-bot>=20.0
    """
    
    def __init__(self,
                 token: str,
                 admin_user_ids: list[str] = None,
                 config: dict[str, Any] = None):
        """Initialize Telegram channel.
        
        Args:
            token: Bot token from @BotFather
            admin_user_ids: User IDs permitted to approve sensitive operations
            config: Additional configuration
        """
        if not HAS_TELEGRAM:
            raise ImportError(
                "python-telegram-bot is required for Telegram channel. "
                "Install with: pip install closeclaw[telegram] "
                "or: pip install python-telegram-bot>=20.0"
            )
        
        super().__init__(channel_type=ChannelType.TELEGRAM, config=config)
        self.token = token
        self.admin_user_ids = [str(uid) for uid in (admin_user_ids or [])]
        
        # Message queue: telegram updates -> agent processing
        self._message_queue: asyncio.Queue[Optional[Message]] = asyncio.Queue()
        
        # Auth response futures: auth_request_id -> Future[AuthorizationResponse]
        self._auth_futures: dict[str, asyncio.Future] = {}
        
        # Telegram application
        self._app: Optional[Application] = None
    
    async def start(self) -> None:
        """Start Telegram bot with long polling."""
        self._running = True
        
        # Build application
        self._app = Application.builder().token(self.token).build()
        
        # Register handlers
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        self._app.add_handler(
            CallbackQueryHandler(self._on_callback_query)
        )
        
        # Raw update handler for debug
        from telegram.ext import TypeHandler
        async def _raw_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
            import json
            logger.info(f"[DEBUG] RAW UPDATE RECEIVED: {update.to_json()}")
        
        self._app.add_handler(TypeHandler(Update, _raw_update), group=-1)
        
        # Initialize and start polling
        from telegram.constants import UpdateType
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
        logger.info("Telegram channel started (long polling, ALL updates allowed)")
    
    async def stop(self) -> None:
        """Stop Telegram bot."""
        self._running = False
        
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        
        # Signal message queue to stop
        await self._message_queue.put(None)
        
        # Cancel pending auth futures
        for future in self._auth_futures.values():
            if not future.done():
                future.cancel()
        self._auth_futures.clear()
        
        logger.info("Telegram channel stopped")
    
    async def receive_message(self) -> Optional[Message]:
        """Get next message from Telegram (blocking wait on queue)."""
        if not self._running:
            return None
        
        message = await self._message_queue.get()
        return message
    
    async def send_response(self, response: dict[str, Any]) -> None:
        """Send response back to user via Telegram.
        
        Routes by response type:
        - "response": Normal text reply
        - "auth_request": Inline Keyboard HITL confirmation
        - "task_completed": Task completion notification
        - "error": Error message
        """
        resp_type = response.get("type", "response")
        chat_id = response.get("_chat_id")
        
        if not chat_id or not self._app:
            logger.warning("Cannot send response: no chat_id or app not initialized")
            return
        
        bot = self._app.bot
        
        # Helper to split long messages to avoid Telegram 4096-char limit.
        def chunk_text(text: str, limit: int = TELEGRAM_SAFE_CHUNK_SIZE) -> list[str]:
            if len(text) <= limit:
                return [text]

            chunks: list[str] = []
            remaining = text
            while len(remaining) > limit:
                split_at = remaining.rfind("\n", 0, limit)
                if split_at <= 0:
                    split_at = limit
                chunks.append(remaining[:split_at])
                remaining = remaining[split_at:].lstrip("\n")
            if remaining:
                chunks.append(remaining)
            return chunks

        def _is_markdown_related_error(err: Exception) -> bool:
            text = str(err).lower()
            return "parse entities" in text or "markdown" in text

        def _is_message_too_long_error(err: Exception) -> bool:
            return bool(re.search(r"message.*too long", str(err), flags=re.IGNORECASE))

        # Helper to safely send message with Markdown fallback and chunking
        async def safe_send(text: str, **kwargs: Any) -> None:
            if not text:
                text = "OK"

            chunks = chunk_text(text)
            for idx, chunk in enumerate(chunks):
                chunk_kwargs = dict(kwargs)
                if idx > 0 and "reply_markup" in chunk_kwargs:
                    chunk_kwargs.pop("reply_markup")

                try:
                    await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown", **chunk_kwargs)
                except Exception as e:
                    if "chat not found" in str(e).lower():
                        logger.warning("Telegram chat not found for chat_id=%s; dropping outbound message", chat_id)
                        return

                    if _is_markdown_related_error(e):
                        logger.warning(f"Telegram Markdown parse error, falling back to plain text: {e}")
                        try:
                            await bot.send_message(chat_id=chat_id, text=chunk, **chunk_kwargs)
                        except Exception as plain_err:
                            if _is_message_too_long_error(plain_err) and len(chunk) > TELEGRAM_MESSAGE_LIMIT:
                                for tiny in chunk_text(chunk, limit=TELEGRAM_MESSAGE_LIMIT):
                                    await bot.send_message(chat_id=chat_id, text=tiny)
                            elif "chat not found" in str(plain_err).lower():
                                logger.warning("Telegram chat not found for chat_id=%s; dropping outbound message", chat_id)
                                return
                            else:
                                raise
                    elif _is_message_too_long_error(e) and len(chunk) > TELEGRAM_MESSAGE_LIMIT:
                        for tiny in chunk_text(chunk, limit=TELEGRAM_MESSAGE_LIMIT):
                            await bot.send_message(chat_id=chat_id, text=tiny)
                    else:
                        raise
        
        if resp_type in {"response", "assistant_message"}:
            text = response.get("response", "")
            tool_calls = response.get("tool_calls", [])
            tool_results = response.get("tool_results", [])
            
            # Build response text
            parts = []
            
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "?") if isinstance(tc, dict) else str(tc)
                    parts.append(f"[TOOL] *Tool:* `{name}`")
            
            if tool_results:
                for tr in tool_results:
                    status = tr.get("status", "?") if isinstance(tr, dict) else str(tr)
                    icon = "[OK]" if status == "success" else ("[TASK]" if status == "task_created" else "[ERR]")
                    parts.append(f"{icon} *Status:* `{status}`")

                    if isinstance(tr, dict):
                        metadata = tr.get("metadata") or {}
                        if metadata.get("auth_mode") == "consensus":
                            decision = metadata.get("guardian_decision") or "approve"
                            parts.append(f"[GUARDIAN] {decision}")
            
            if text:
                parts.append(text)
            
            full_text = "\n".join(parts) if parts else "OK"
            await safe_send(full_text)
            
        elif resp_type == "auth_request":
            await self._send_auth_request_message(chat_id, response)
        
        elif resp_type == "task_completed":
            task_id = response.get("task_id", "?")
            status = response.get("status", "?")
            result = response.get("result", "")
            error = response.get("error")
            
            text = f"*Task Completed*\nTask: `{task_id}` | Status: `{status}`"
            if error:
                text += f"\nError: {error}"
            elif result:
                result_str = str(result)[:500]
                text += f"\nResult: {result_str}"
            
            await safe_send(text)

        elif resp_type == "tool_progress":
            tool_name = response.get("tool_name", "unknown")
            status = response.get("status", "unknown")
            text = f"[TOOL] tool=`{tool_name}` status=`{status}`"
            target_file = response.get("target_file")
            if target_file:
                text += f"\nfile=`{target_file}`"
            await safe_send(text)
            
        elif resp_type == "error":
            error = response.get("error", "Unknown error")
            await safe_send(f"*Error:* {error}")
    
    async def send_auth_request(self,
                                auth_request_id: str,
                                tool_name: str,
                                description: str,
                                diff_preview: Optional[str] = None,
                                reason: Optional[str] = None,
                                auth_mode: Optional[str] = None) -> None:
        """Send HITL confirmation with Inline Keyboard buttons.
        
        Note: This is called internally via send_response() routing.
        Direct calls should use _send_auth_request_message().
        """
        # This method exists for BaseChannel compliance
        # Actual implementation is in _send_auth_request_message()
        pass
    
    async def _send_auth_request_message(self, chat_id: int, response: dict[str, Any]) -> None:
        """Send HITL confirmation message with Inline Keyboard."""
        auth_request_id = response.get("auth_request_id", "unknown")
        tool_name = response.get("tool_name", "unknown")
        description = response.get("description", "")
        diff_preview = response.get("diff_preview")
        reason = response.get("reason")
        auth_mode = response.get("auth_mode")
        
        # Build message text
        text_parts = [
            "*Sensitive Operation - Authorization Required*",
            f"Tool: `{tool_name}`",
            f"Description: {description}",
        ]
        if auth_mode:
            text_parts.append(f"Mode: `{auth_mode}`")
        if reason:
            text_parts.append(f"Reason: {reason}")
        
        if diff_preview:
            # Format diff for Telegram (use code block)
            text_parts.append(f"\n```\n{diff_preview[:1000]}\n```")
        
        text = "\n".join(text_parts)
        
        # Create Inline Keyboard
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve", callback_data=f"auth_yes:{auth_request_id}"),
                InlineKeyboardButton("Reject", callback_data=f"auth_no:{auth_request_id}"),
            ]
        ])
        
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        except Exception as e:
            if "parse entities" in str(e).lower() or "markdown" in str(e).lower():
                logger.warning(f"Telegram Markdown parse error in auth request, falling back to plain text: {e}")
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
            else:
                raise
        
        logger.info(f"Auth request sent via Telegram: {auth_request_id}")
    
    async def wait_for_auth_response(self,
                                      auth_request_id: str,
                                      timeout: float = 300.0) -> Optional[AuthorizationResponse]:
        """Wait for user's Inline Keyboard callback response."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._auth_futures[auth_request_id] = future
        logger.info(f"[DEBUG] wait_for_auth_response: created future for {auth_request_id}, timeout={timeout}s")
        logger.info(f"[DEBUG] Active auth_futures: {list(self._auth_futures.keys())}")
        
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"[DEBUG] wait_for_auth_response: future resolved for {auth_request_id}")
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Auth request timed out: {auth_request_id}")
            return None
        finally:
            self._auth_futures.pop(auth_request_id, None)
    
    # ---- Telegram Handler Callbacks ----
    
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages from Telegram."""
        if not update.message or not update.message.text:
            return
        
        tg_msg = update.message
        
        # Convert to internal Message
        message = self._create_message(
            message_id=str(tg_msg.message_id),
            sender_id=str(tg_msg.from_user.id),
            sender_name=tg_msg.from_user.full_name or tg_msg.from_user.username or "Unknown",
            content=tg_msg.text,
            chat_id=tg_msg.chat_id,
            telegram_update_id=update.update_id,
        )
        
        # Store chat_id for response routing
        message.metadata["_chat_id"] = tg_msg.chat_id
        
        await self._message_queue.put(message)
        logger.info(f"Telegram message received from {message.sender_name}: {message.content[:50]}")
    
    async def _on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle Inline Keyboard button clicks (auth responses).
        
        Callback data format: "auth_yes:<auth_request_id>" or "auth_no:<auth_request_id>"
        """
        logger.info("[DEBUG] _on_callback_query FIRED")
        query = update.callback_query
        if not query or not query.data:
            logger.info("[DEBUG] _on_callback_query: no query or no data, returning")
            return
        
        logger.info(f"[DEBUG] Callback data: {query.data}, from user: {query.from_user.id}")
        await query.answer()  # Acknowledge the callback
        
        data = query.data
        user_id = str(query.from_user.id)
        
        # Parse callback data
        if not (data.startswith("auth_yes:") or data.startswith("auth_no:")):
            return
        
        parts = data.split(":", 1)
        if len(parts) != 2:
            return
        
        action, auth_request_id = parts
        approved = action == "auth_yes"
        
        # Verify admin permission
        if user_id not in self.admin_user_ids:
            await query.edit_message_text(
                text="You are not authorized to approve this operation.",
            )
            logger.warning(f"Unauthorized auth attempt from user {user_id}")
            return
        
        # Create AuthorizationResponse
        auth_response = AuthorizationResponse(
            auth_request_id=auth_request_id,
            user_id=user_id,
            approved=approved,
            metadata={"_chat_id": query.message.chat_id} if query.message else {}
        )
        
        # Resolve the waiting Future
        logger.info(f"[DEBUG] Looking up future for auth_request_id={auth_request_id}")
        logger.info(f"[DEBUG] Available futures: {list(self._auth_futures.keys())}")
        future = self._auth_futures.get(auth_request_id)
        if future and not future.done():
            logger.info(f"[DEBUG] Resolving future for {auth_request_id}")
            future.set_result(auth_response)
        else:
            logger.warning(f"[DEBUG] No active future for {auth_request_id}! future={future}")
        
        # Update the message to show result
        status_text = "Approved" if approved else "Rejected"
        user_name = query.from_user.full_name or query.from_user.username or user_id
        
        try:
            original_text = query.message.text or ""
            await query.edit_message_text(
                text=f"{original_text}\n\n*Result:* {status_text} by {user_name}",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to edit auth result message: {e}")
        
        logger.info(f"Auth response received: {auth_request_id} -> {status_text} by {user_name}")


