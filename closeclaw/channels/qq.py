from __future__ import annotations

"""QQ channel integration via qq-botpy."""

import asyncio
import logging
import re
from collections import deque
from typing import Any, Optional

from .base import BaseChannel
from ..types import Message, ChannelType, AuthorizationResponse

logger = logging.getLogger(__name__)

try:
    import botpy  # type: ignore[import-not-found]
    from botpy.message import C2CMessage, GroupMessage  # type: ignore[import-not-found]
    HAS_QQ = True
except ImportError:
    HAS_QQ = False
    botpy = None
    C2CMessage = None
    GroupMessage = None


class QQChannel(BaseChannel):
    """QQ channel using qq-botpy SDK."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        admin_user_ids: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ):
        if not HAS_QQ:
            raise ImportError(
                "qq-botpy is required for QQ channel. "
                "Install with: pip install qq-botpy"
            )

        super().__init__(channel_type=ChannelType.QQ, config=config)
        self.app_id = app_id
        self.app_secret = app_secret
        self.admin_user_ids = [str(uid) for uid in (admin_user_ids or [])]

        self._message_queue: asyncio.Queue[Optional[Message]] = asyncio.Queue()
        self._auth_futures: dict[str, asyncio.Future] = {}
        self._client: Any = None
        self._client_task: Optional[asyncio.Task] = None
        self._chat_type_cache: dict[str, str] = {}
        self._processed_ids: deque[str] = deque(maxlen=1000)
        self._msg_seq = 1

    async def start(self) -> None:
        self._running = True

        intents = botpy.Intents(public_messages=True, direct_message=True)
        channel = self

        class _QQClient(botpy.Client):
            def __init__(self):
                super().__init__(intents=intents, ext_handlers=False)

            async def on_ready(self):
                logger.info("QQ channel started")

            async def on_c2c_message_create(self, message: Any):
                await channel._on_message(message, is_group=False)

            async def on_group_at_message_create(self, message: Any):
                await channel._on_message(message, is_group=True)

            async def on_direct_message_create(self, message):
                await channel._on_message(message, is_group=False)

        self._client = _QQClient()
        self._client_task = asyncio.create_task(
            self._client.start(appid=self.app_id, secret=self.app_secret)
        )

    async def stop(self) -> None:
        self._running = False

        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass

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
        logger.info("QQ channel stopped")

    async def receive_message(self) -> Optional[Message]:
        if not self._running:
            return None
        return await self._message_queue.get()

    async def send_response(self, response: dict[str, Any]) -> None:
        if not self._client:
            return

        chat_id = str(response.get("_chat_id") or "")
        if not chat_id:
            logger.warning("Cannot send QQ response: no _chat_id")
            return

        resp_type = response.get("type", "response")
        if resp_type in {"response", "assistant_message"}:
            text = response.get("response", "") or "OK"
        elif resp_type == "auth_request":
            req_id = response.get("auth_request_id", "unknown")
            text = (
                "[AUTH REQUIRED]\n"
                f"request={req_id}\n"
                f"tool={response.get('tool_name', 'unknown')}\n"
                f"reply with: approve {req_id} OR reject {req_id}"
            )
        elif resp_type == "task_completed":
            text = (
                f"[TASK] id={response.get('task_id', '?')} "
                f"status={response.get('status', '?')}"
            )
            if response.get("error"):
                text += f"\nerror={response['error']}"
        elif resp_type == "tool_progress":
            text = (
                f"[TOOL] tool={response.get('tool_name', 'unknown')} "
                f"status={response.get('status', 'unknown')}"
            )
            target_file = response.get("target_file")
            if target_file:
                text += f"\nfile={target_file}"
        elif resp_type == "error":
            text = f"[ERROR] {response.get('error', 'Unknown error')}"
        else:
            text = str(response)

        self._msg_seq += 1
        msg_id = response.get("message_id") or str(self._msg_seq)
        chat_type = self._chat_type_cache.get(chat_id, "c2c")

        try:
            if chat_type == "group":
                await self._client.api.post_group_message(
                    group_openid=chat_id,
                    msg_type=0,
                    content=text[:1800],
                    msg_id=msg_id,
                    msg_seq=self._msg_seq,
                )
            else:
                await self._client.api.post_c2c_message(
                    openid=chat_id,
                    msg_type=0,
                    content=text[:1800],
                    msg_id=msg_id,
                    msg_seq=self._msg_seq,
                )
        except Exception as exc:
            logger.error("Error sending QQ response: %s", exc)

    async def send_auth_request(
        self,
        auth_request_id: str,
        tool_name: str,
        description: str,
        diff_preview: Optional[str] = None,
        reason: Optional[str] = None,
        auth_mode: Optional[str] = None,
    ) -> None:
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

    async def _on_message(self, data: Any, is_group: bool = False) -> None:
        try:
            msg_id = str(getattr(data, "id", ""))
            if msg_id and msg_id in self._processed_ids:
                return
            if msg_id:
                self._processed_ids.append(msg_id)

            content = (getattr(data, "content", "") or "").strip()
            if not content:
                return

            if is_group:
                chat_id = str(getattr(data, "group_openid", ""))
                author = getattr(data, "author", None)
                sender_id = str(getattr(author, "member_openid", ""))
                self._chat_type_cache[chat_id] = "group"
            else:
                author = getattr(data, "author", None)
                sender_id = str(
                    getattr(author, "id", None)
                    or getattr(author, "user_openid", "")
                )
                chat_id = sender_id
                self._chat_type_cache[chat_id] = "c2c"

            if self._try_resolve_auth_from_message(sender_id=sender_id, content=content):
                return

            message = self._create_message(
                message_id=msg_id or f"qq_{asyncio.get_running_loop().time()}",
                sender_id=sender_id,
                sender_name=sender_id,
                content=content,
                _chat_id=chat_id,
                message_id_source=msg_id,
            )
            await self._message_queue.put(message)
        except Exception:
            logger.exception("Error handling QQ message")

    def _try_resolve_auth_from_message(self, sender_id: str, content: str) -> bool:
        match = re.match(r"^/?(approve|reject)\s+([A-Za-z0-9_\-:.]+)$", content.strip(), flags=re.IGNORECASE)
        if not match:
            return False

        action, auth_request_id = match.group(1).lower(), match.group(2)
        if self.admin_user_ids and sender_id not in self.admin_user_ids:
            logger.warning("Unauthorized QQ auth attempt from sender_id=%s", sender_id)
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
