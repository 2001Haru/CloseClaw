from __future__ import annotations

"""Discord channel integration (discord.py based)."""

import asyncio
import logging
import re
from typing import Any, Optional

from .base import BaseChannel
from ..types import Message, ChannelType, AuthorizationResponse

logger = logging.getLogger(__name__)

try:
    import discord  # type: ignore[import-not-found]
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False


class DiscordChannel(BaseChannel):
    """Discord Bot channel via discord.py."""

    def __init__(
        self,
        token: str,
        admin_user_ids: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ):
        if not HAS_DISCORD:
            raise ImportError(
                "discord.py is required for Discord channel. "
                "Install with: pip install discord.py"
            )

        super().__init__(channel_type=ChannelType.DISCORD, config=config)
        self.token = token
        self.admin_user_ids = [str(uid) for uid in (admin_user_ids or [])]
        self._message_queue: asyncio.Queue[Optional[Message]] = asyncio.Queue()
        self._auth_futures: dict[str, asyncio.Future] = {}
        self._client: Optional[discord.Client] = None
        self._client_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._running = True

        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True
        intents.guilds = True

        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:
            logger.info("Discord channel started as %s", client.user)

        @client.event
        async def on_message(raw_message: Any) -> None:
            if raw_message.author == client.user:
                return

            sender_id = str(raw_message.author.id)
            content = (raw_message.content or "").strip()
            if not content:
                return

            if self._try_resolve_auth_from_message(sender_id=sender_id, content=content):
                return

            msg = self._create_message(
                message_id=str(raw_message.id),
                sender_id=sender_id,
                sender_name=getattr(raw_message.author, "display_name", None)
                or getattr(raw_message.author, "name", "Unknown"),
                content=content,
                _chat_id=str(raw_message.channel.id),
                guild_id=str(raw_message.guild.id) if raw_message.guild else None,
            )
            await self._message_queue.put(msg)

        self._client = client
        self._client_task = asyncio.create_task(client.start(self.token))

    async def stop(self) -> None:
        self._running = False

        if self._client:
            await self._client.close()

        if self._client_task:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            self._client_task = None

        for fut in self._auth_futures.values():
            if not fut.done():
                fut.cancel()
        self._auth_futures.clear()

        await self._message_queue.put(None)
        logger.info("Discord channel stopped")

    async def receive_message(self) -> Optional[Message]:
        if not self._running:
            return None
        return await self._message_queue.get()

    async def send_response(self, response: dict[str, Any]) -> None:
        if not self._client:
            return

        chat_id = response.get("_chat_id")
        if not chat_id:
            logger.warning("Cannot send Discord response: no _chat_id")
            return

        channel = self._client.get_channel(int(chat_id))
        if channel is None:
            try:
                channel = await self._client.fetch_channel(int(chat_id))
            except Exception as exc:
                logger.warning("Discord channel not found (%s): %s", chat_id, exc)
                return

        resp_type = response.get("type", "response")
        token_prefix = str(response.get("_token_usage_prefix", "") or "").strip()
        raw_reply_to_message_id = response.get("_reply_to_message_id")
        reply_to_message_id: Optional[int] = None
        if raw_reply_to_message_id is not None:
            try:
                reply_to_message_id = int(raw_reply_to_message_id)
            except (TypeError, ValueError):
                reply_to_message_id = None
        text = ""

        if resp_type in {"response", "assistant_message"}:
            text = response.get("response", "") or "OK"
        elif resp_type == "auth_request":
            req_id = response.get("auth_request_id", "unknown")
            tool_name = response.get("tool_name", "unknown")
            desc = response.get("description", "")
            diff = response.get("diff_preview")
            lines = [
                "[AUTH REQUIRED]",
                f"request={req_id}",
                f"tool={tool_name}",
                f"desc={desc}",
                f"reply with: approve {req_id} OR reject {req_id}",
            ]
            if diff:
                lines.append(f"diff={str(diff)[:800]}")
            text = "\n".join(lines)
        elif resp_type == "task_completed":
            text = (
                f"[TASK] id={response.get('task_id', '?')} "
                f"status={response.get('status', '?')}"
            )
            if response.get("error"):
                text += f"\nerror={response['error']}"
        elif resp_type == "tool_progress":
            tool = response.get("tool_name", "unknown")
            status = response.get("status", "unknown")
            text = f"[TOOL] tool={tool} status={status}"
            target_file = response.get("target_file")
            if target_file:
                text += f"\nfile={target_file}"
        elif resp_type == "error":
            text = f"[ERROR] {response.get('error', 'Unknown error')}"
        else:
            text = str(response)

        if resp_type in {"response", "assistant_message"} and token_prefix:
            text = f"{token_prefix}\n{text}"
        if len(text) > 1900:
            text = text[:1900] + "..."

        if resp_type in {"response", "assistant_message"} and reply_to_message_id is not None:
            try:
                partial = channel.get_partial_message(reply_to_message_id)
                await channel.send(text, reference=partial, mention_author=False)
                return
            except Exception as exc:
                logger.debug("Discord reply reference failed, fallback to normal send: %s", exc)

        await channel.send(text)

    async def send_auth_request(
        self,
        auth_request_id: str,
        tool_name: str,
        description: str,
        diff_preview: Optional[str] = None,
        reason: Optional[str] = None,
        auth_mode: Optional[str] = None,
    ) -> None:
        # Routed via send_response(auth_request)
        return

    async def wait_for_auth_response(
        self,
        auth_request_id: str,
        timeout: float = 300.0,
    ) -> Optional[AuthorizationResponse]:
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._auth_futures[auth_request_id] = future

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._auth_futures.pop(auth_request_id, None)

    def _try_resolve_auth_from_message(self, sender_id: str, content: str) -> bool:
        match = re.match(r"^/?(approve|reject)\s+([A-Za-z0-9_\-:.]+)$", content.strip(), flags=re.IGNORECASE)
        if not match:
            return False

        action, auth_request_id = match.group(1).lower(), match.group(2)
        if self.admin_user_ids and sender_id not in self.admin_user_ids:
            logger.warning("Unauthorized Discord auth attempt from sender_id=%s", sender_id)
            return True

        future = self._auth_futures.get(auth_request_id)
        if future and not future.done():
            future.set_result(
                AuthorizationResponse(
                    auth_request_id=auth_request_id,
                    user_id=sender_id,
                    approved=(action == "approve"),
                )
            )
        return True
