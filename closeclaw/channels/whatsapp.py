from __future__ import annotations

"""WhatsApp channel integration via WebSocket bridge."""

import asyncio
import json
import logging
import re
from typing import Any, Optional

from .base import BaseChannel
from ..types import Message, ChannelType, AuthorizationResponse

logger = logging.getLogger(__name__)

try:
    import websockets  # type: ignore[import-not-found]
    HAS_WHATSAPP_BRIDGE = True
except ImportError:
    HAS_WHATSAPP_BRIDGE = False


class WhatsAppChannel(BaseChannel):
    """WhatsApp channel using a bridge process (e.g. Baileys bridge)."""

    def __init__(
        self,
        bridge_url: str,
        admin_user_ids: list[str] | None = None,
        bridge_token: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        if not HAS_WHATSAPP_BRIDGE:
            raise ImportError(
                "websockets is required for WhatsApp bridge channel. "
                "Install with: pip install websockets"
            )

        super().__init__(channel_type=ChannelType.WHATSAPP, config=config)
        self.bridge_url = bridge_url
        self.bridge_token = bridge_token
        self.admin_user_ids = [str(uid) for uid in (admin_user_ids or [])]

        self._message_queue: asyncio.Queue[Optional[Message]] = asyncio.Queue()
        self._auth_futures: dict[str, asyncio.Future] = {}
        self._bridge_task: Optional[asyncio.Task] = None
        self._ws = None
        self._connected = False

    async def start(self) -> None:
        self._running = True
        self._bridge_task = asyncio.create_task(self._bridge_loop())
        logger.info("WhatsApp channel started (bridge=%s)", self.bridge_url)

    async def stop(self) -> None:
        self._running = False

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._bridge_task:
            self._bridge_task.cancel()
            try:
                await self._bridge_task
            except asyncio.CancelledError:
                pass
            self._bridge_task = None

        for fut in self._auth_futures.values():
            if not fut.done():
                fut.cancel()
        self._auth_futures.clear()

        await self._message_queue.put(None)
        logger.info("WhatsApp channel stopped")

    async def receive_message(self) -> Optional[Message]:
        if not self._running:
            return None
        return await self._message_queue.get()

    async def send_response(self, response: dict[str, Any]) -> None:
        chat_id = response.get("_chat_id")
        if not chat_id:
            logger.warning("Cannot send WhatsApp response: no _chat_id")
            return

        resp_type = response.get("type", "response")
        token_prefix = str(response.get("_token_usage_prefix", "") or "").strip()
        text = ""

        if resp_type in {"response", "assistant_message"}:
            text = response.get("response", "") or "OK"
        elif resp_type == "auth_request":
            req_id = response.get("auth_request_id", "unknown")
            text = (
                "[AUTH REQUIRED]\n"
                f"request={req_id}\n"
                f"tool={response.get('tool_name', 'unknown')}\n"
                f"desc={response.get('description', '')}\n"
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

        if resp_type in {"response", "assistant_message"} and token_prefix:
            text = f"{token_prefix}\n{text}"
        await self._bridge_send(
            {
                "type": "send",
                "to": str(chat_id),
                "text": text[:3500],
            }
        )

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

    async def _bridge_loop(self) -> None:
        while self._running:
            try:
                async with websockets.connect(self.bridge_url) as ws:
                    self._ws = ws
                    self._connected = True

                    if self.bridge_token:
                        await ws.send(json.dumps({"type": "auth", "token": self.bridge_token}))

                    async for raw in ws:
                        await self._process_bridge_event(raw)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("WhatsApp bridge connection error: %s", exc)
                await asyncio.sleep(3.0)
            finally:
                self._ws = None
                self._connected = False

    async def _bridge_send(self, payload: dict[str, Any]) -> None:
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def _process_bridge_event(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from WhatsApp bridge")
            return

        event_type = data.get("type")

        if event_type == "auth_response":
            auth_request_id = str(data.get("auth_request_id", ""))
            approved = bool(data.get("approved", False))
            user_id = str(data.get("user_id", "unknown"))
            future = self._auth_futures.get(auth_request_id)
            if future and not future.done():
                future.set_result(
                    AuthorizationResponse(
                        auth_request_id=auth_request_id,
                        user_id=user_id,
                        approved=approved,
                    )
                )
            return

        if event_type != "message":
            return

        sender_raw = str(data.get("sender") or data.get("pn") or "")
        sender_id = sender_raw.split("@")[0] if "@" in sender_raw else sender_raw
        content = str(data.get("content", "")).strip()
        chat_id = str(data.get("chat_id") or data.get("sender") or "")
        msg_id = str(data.get("id") or "")

        if not sender_id or not content:
            return

        if self._try_resolve_auth_from_message(sender_id=sender_id, content=content):
            return

        message = self._create_message(
            message_id=msg_id or f"wa_{asyncio.get_running_loop().time()}",
            sender_id=sender_id,
            sender_name=sender_id,
            content=content,
            _chat_id=chat_id,
            is_group=bool(data.get("isGroup", False)),
        )
        await self._message_queue.put(message)

    def _try_resolve_auth_from_message(self, sender_id: str, content: str) -> bool:
        match = re.match(r"^/?(approve|reject)\s+([A-Za-z0-9_\-:.]+)$", content.strip(), flags=re.IGNORECASE)
        if not match:
            return False

        action, auth_request_id = match.group(1).lower(), match.group(2)
        if self.admin_user_ids and sender_id not in self.admin_user_ids:
            logger.warning("Unauthorized WhatsApp auth attempt from sender_id=%s", sender_id)
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
