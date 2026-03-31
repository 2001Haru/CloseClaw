"""Feishu (Lark) channel - Integration via httpx + Feishu Open Platform REST API.

Uses httpx for lightweight, direct API calls instead of heavy SDK.
Implements event subscription via webhook for message reception and
Interactive Cards for HITL (Human-in-the-Loop) sensitive operation confirmations.

From Planning.md:
    "Retain Feishu (domestic collaboration)"
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from .audio_transcription import AudioTranscriptionError, AudioTranscriptionService
from .base import BaseChannel
from ..types import Message, ChannelType, AuthorizationResponse

logger = logging.getLogger(__name__)

# Feishu API endpoints
FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"
FEISHU_TOKEN_URL = f"{FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal"
FEISHU_SEND_MSG_URL = f"{FEISHU_BASE_URL}/im/v1/messages"
FEISHU_REPLY_MSG_URL_TEMPLATE = f"{FEISHU_BASE_URL}/im/v1/messages/{{message_id}}/reply"


class FeishuChannel(BaseChannel):
    """Feishu (Lark) channel via httpx + REST API.
    
    Features:
    - Webhook event subscription for message reception
    - Interactive Card for HITL confirmation (need_auth tools)
    - Auto-refreshing tenant_access_token
    - Admin user verification for auth approvals
    
    Requirements:
        - httpx (already in base dependencies)
        - Feishu app configured with:
          - Message event subscription
          - Card action callback
          - Bot capability enabled
    
    Config needed in config.yaml:
        channels:
          - type: "feishu"
            enabled: true
            token: ${FEISHU_APP_ID}
            webhook_url: ${FEISHU_APP_SECRET}
            metadata:
              verification_token: ${FEISHU_VERIFICATION_TOKEN}
              encrypt_key: ${FEISHU_ENCRYPT_KEY}  # optional
    """
    
    def __init__(self,
                 app_id: str,
                 app_secret: str,
                 admin_user_ids: list[str] = None,
                 verification_token: str = "",
                 webhook_port: int = 9000,
                 config: dict[str, Any] = None):
        """Initialize Feishu channel.
        
        Args:
            app_id: Feishu app ID
            app_secret: Feishu app secret
            admin_user_ids: User IDs permitted to approve sensitive operations
            verification_token: Webhook verification token
            webhook_port: Port for webhook HTTP server
            config: Additional configuration
        """
        super().__init__(channel_type=ChannelType.FEISHU, config=config)
        self.app_id = app_id
        self.app_secret = app_secret
        self.admin_user_ids = [str(uid) for uid in (admin_user_ids or [])]
        self.verification_token = verification_token
        self.webhook_port = webhook_port
        
        # HTTP client (reusable)
        self._client: Optional[httpx.AsyncClient] = None
        
        # Token management
        self._tenant_access_token: Optional[str] = None
        self._token_expires_at: float = 0
        
        # Message queue: feishu events -> agent processing
        self._message_queue: asyncio.Queue[Optional[Message]] = asyncio.Queue()
        
        # Auth response futures
        self._auth_futures: dict[str, asyncio.Future] = {}
        
        # Webhook server
        self._server = None
        
        # Track processed message IDs to avoid duplicates
        self._processed_message_ids: set[str] = set()
        self._audio_transcriber = AudioTranscriptionService.from_channel_config(
            channel_config=self.config if isinstance(self.config, dict) else {},
            channel_name="feishu",
        )
    
    async def start(self) -> None:
        """Start Feishu channel: refresh token + start webhook server."""
        self._running = True
        self._client = httpx.AsyncClient(timeout=30.0)
        
        # Get initial access token
        await self._refresh_token()
        
        # Start webhook HTTP server
        await self._start_webhook_server()
        
        logger.info(f"Feishu channel started (webhook on port {self.webhook_port})")
    
    async def stop(self) -> None:
        """Stop Feishu channel."""
        self._running = False
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        if self._client:
            await self._client.aclose()
        
        await self._message_queue.put(None)
        
        for future in self._auth_futures.values():
            if not future.done():
                future.cancel()
        self._auth_futures.clear()
        
        logger.info("Feishu channel stopped")
    
    async def receive_message(self) -> Optional[Message]:
        """Get next message from Feishu (blocking wait on queue)."""
        if not self._running:
            return None
        return await self._message_queue.get()
    
    async def send_response(self, response: dict[str, Any]) -> None:
        """Send response back to user via Feishu."""
        resp_type = response.get("type", "response")
        chat_id = response.get("_chat_id")
        token_prefix = str(response.get("_token_usage_prefix", "") or "").strip()
        
        if not chat_id:
            logger.warning("Cannot send response: no chat_id")
            return

        if resp_type in {"response", "assistant_message"}:
            text = response.get("response", "OK")
            tool_results = response.get("tool_results", [])
            reply_to_message_id = str(response.get("_reply_to_message_id", "") or "").strip()
            lines: list[str] = [text]

            if tool_results:
                for tr in tool_results:
                    if not isinstance(tr, dict):
                        continue
                    metadata = tr.get("metadata") or {}
                    if metadata.get("auth_mode") == "consensus":
                        decision = metadata.get("guardian_decision") or "approve"
                        lines.append(f"[GUARDIAN] {decision}")

            text = "\n".join(lines)
            if token_prefix:
                text = f"{token_prefix}\n{text}"
            if reply_to_message_id:
                sent = await self._reply_text_message(reply_to_message_id, text)
                if not sent:
                    await self._send_text_message(chat_id, text)
            else:
                await self._send_text_message(chat_id, text)
        
        elif resp_type == "auth_request":
            await self._send_auth_card(chat_id, response)
        
        elif resp_type == "task_completed":
            task_id = response.get("task_id", "?")
            status = response.get("status", "?")
            result = response.get("result", "")
            error = response.get("error")
            
            text = f"Task Completed\nTask: {task_id} | Status: {status}"
            if error:
                text += f"\nError: {error}"
            elif result:
                text += f"\nResult: {str(result)[:500]}"
            
            await self._send_text_message(chat_id, text)

        elif resp_type == "tool_progress":
            tool_name = response.get("tool_name", "unknown")
            status = response.get("status", "unknown")
            text = f"[TOOL] tool={tool_name} status={status}"
            target_file = response.get("target_file")
            if target_file:
                text += f"\nfile={target_file}"
            await self._send_text_message(chat_id, text)
        
        elif resp_type == "error":
            error = response.get("error", "Unknown error")
            await self._send_text_message(chat_id, f"Error: {error}")
    
    async def send_auth_request(self,
                                auth_request_id: str,
                                tool_name: str,
                                description: str,
                                diff_preview: Optional[str] = None,
                                reason: Optional[str] = None,
                                auth_mode: Optional[str] = None) -> None:
        """Send HITL confirmation via Feishu Interactive Card."""
        pass  # Implemented via send_response -> _send_auth_card
    
    async def wait_for_auth_response(self,
                                      auth_request_id: str,
                                      timeout: float = 300.0) -> Optional[AuthorizationResponse]:
        """Wait for user's card button callback."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._auth_futures[auth_request_id] = future
        
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Feishu auth request timed out: {auth_request_id}")
            return None
        finally:
            self._auth_futures.pop(auth_request_id, None)
    
    # ---- Feishu API Methods ----
    
    async def _refresh_token(self) -> None:
        """Refresh tenant_access_token from Feishu API."""
        try:
            resp = await self._client.post(
                FEISHU_TOKEN_URL,
                json={
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            
            self._tenant_access_token = data.get("tenant_access_token")
            expire = data.get("expire", 7200)
            self._token_expires_at = time.time() + expire - 300  # Refresh 5 min early
            
            logger.info("Feishu tenant_access_token refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh Feishu token: {e}")
            raise
    
    async def _ensure_token(self) -> str:
        """Ensure token is fresh, refresh if needed."""
        if not self._tenant_access_token or time.time() >= self._token_expires_at:
            await self._refresh_token()
        return self._tenant_access_token
    
    async def _send_text_message(self, chat_id: str, text: str) -> None:
        """Send a plain text message to a Feishu chat."""
        token = await self._ensure_token()
        
        try:
            resp = await self._client.post(
                FEISHU_SEND_MSG_URL,
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
            )
            resp.raise_for_status()
            logger.debug(f"Feishu message sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send Feishu message: {e}")

    async def _download_message_resource_to_path(
        self,
        message_id: str,
        file_key: str,
        target_path: Path,
        msg_type: str,
    ) -> None:
        token = await self._ensure_token()
        url = f"{FEISHU_BASE_URL}/im/v1/messages/{message_id}/resources/{file_key}"
        resource_types = ["audio"] if msg_type == "audio" else ["file", "audio"]
        last_exc: Optional[Exception] = None
        for resource_type in resource_types:
            try:
                resp = await self._client.get(
                    url,
                    params={"type": resource_type},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                target_path.write_bytes(resp.content)
                return
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"Failed to download Feishu message resource: {last_exc}")

    async def _reply_text_message(self, message_id: str, text: str) -> bool:
        """Reply to an existing Feishu message by message_id."""
        token = await self._ensure_token()
        reply_url = FEISHU_REPLY_MSG_URL_TEMPLATE.format(message_id=message_id)
        try:
            resp = await self._client.post(
                reply_url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
            )
            resp.raise_for_status()
            logger.debug("Feishu reply sent to message_id=%s", message_id)
            return True
        except Exception as e:
            logger.warning("Failed to send Feishu reply (message_id=%s): %s", message_id, e)
            return False
    
    async def _send_auth_card(self, chat_id: str, response: dict[str, Any]) -> None:
        """Send Interactive Card for HITL confirmation."""
        auth_request_id = response.get("auth_request_id", "unknown")
        tool_name = response.get("tool_name", "unknown")
        description = response.get("description", "")
        diff_preview = response.get("diff_preview", "")
        reason = response.get("reason", "")
        auth_mode = response.get("auth_mode", "")
        
        # Build Interactive Card
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "Sensitive Operation - Authorization Required"},
                "template": "orange",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**Tool:** `{tool_name}`\n**Description:** {description}",
                    },
                },
            ],
        }

        if auth_mode:
            card["elements"].append(
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**Mode:** `{auth_mode}`",
                    },
                }
            )

        if reason:
            card["elements"].append(
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**Reason:** {reason}",
                    },
                }
            )
        
        # Add diff preview if present
        if diff_preview:
            card["elements"].append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**Diff Preview:**\n```\n{diff_preview[:800]}\n```",
                },
            })
        
        # Add action buttons
        card["elements"].append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "Approve"},
                    "type": "primary",
                    "value": {"action": "approve", "auth_request_id": auth_request_id},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "Reject"},
                    "type": "danger",
                    "value": {"action": "reject", "auth_request_id": auth_request_id},
                },
            ],
        })
        
        token = await self._ensure_token()
        
        try:
            resp = await self._client.post(
                FEISHU_SEND_MSG_URL,
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card),
                },
            )
            resp.raise_for_status()
            logger.info(f"Feishu auth card sent: {auth_request_id}")
        except Exception as e:
            logger.error(f"Failed to send Feishu auth card: {e}")
    
    # ---- Webhook Server ----
    
    async def _start_webhook_server(self) -> None:
        """Start a lightweight asyncio HTTP server to receive Feishu events."""
        
        async def handle_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            """Handle incoming HTTP request from Feishu webhook."""
            try:
                # Read HTTP request (simple parsing)
                request_line = await reader.readline()
                headers = {}
                while True:
                    line = await reader.readline()
                    if line == b"\r\n" or line == b"\n" or not line:
                        break
                    if b":" in line:
                        key, value = line.decode().split(":", 1)
                        headers[key.strip().lower()] = value.strip()
                
                # Read body
                content_length = int(headers.get("content-length", "0"))
                body = b""
                if content_length > 0:
                    body = await reader.readexactly(content_length)
                
                # Process event
                response_body = await self._process_webhook_event(body)
                
                # Send HTTP response
                http_response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(response_body)}\r\n"
                    f"\r\n"
                    f"{response_body}"
                )
                writer.write(http_response.encode())
                await writer.drain()
                
            except Exception as e:
                logger.error(f"Webhook request error: {e}")
            finally:
                writer.close()
        
        self._server = await asyncio.start_server(
            handle_request, "0.0.0.0", self.webhook_port
        )
        logger.info(f"Feishu webhook server listening on port {self.webhook_port}")
    
    async def _process_webhook_event(self, body: bytes) -> str:
        """Process incoming Feishu webhook event.
        
        Handles:
        - URL verification challenge
        - im.message.receive_v1 (new messages)
        - card.action.trigger (card button clicks)
        """
        try:
            event_data = json.loads(body)
        except json.JSONDecodeError:
            return json.dumps({"error": "invalid json"})
        
        # Handle URL verification challenge
        if "challenge" in event_data:
            return json.dumps({"challenge": event_data["challenge"]})
        
        # Handle event callback
        event_header = event_data.get("header", {})
        event_type = event_header.get("event_type", "")
        event = event_data.get("event", {})
        
        if event_type == "im.message.receive_v1":
            await self._handle_message_event(event)
        elif event_type == "card.action.trigger":
            await self._handle_card_action(event)
        
        return json.dumps({"code": 0})
    
    async def _handle_message_event(self, event: dict[str, Any]) -> None:
        """Handle incoming message event."""
        message_data = event.get("message", {})
        sender = event.get("sender", {}).get("sender_id", {})
        
        message_id = message_data.get("message_id", "")
        
        # Deduplicate
        if message_id in self._processed_message_ids:
            return
        self._processed_message_ids.add(message_id)
        
        # Limit processed IDs to avoid memory growth
        if len(self._processed_message_ids) > 10000:
            self._processed_message_ids = set(list(self._processed_message_ids)[-5000:])
        
        msg_type = message_data.get("message_type", "")
        content_str = message_data.get("content", "{}")
        try:
            content = json.loads(content_str)
        except json.JSONDecodeError:
            content = {}

        text = str(content.get("text", "") or "").strip() if isinstance(content, dict) else ""
        file_key = str(content.get("file_key", "") or content.get("audio_key", "") or "") if isinstance(content, dict) else ""
        mime_type = str(content.get("mime_type", "") or "") if isinstance(content, dict) else ""
        duration = content.get("duration") if isinstance(content, dict) else None

        transcript: Optional[str] = None
        stt_error: Optional[str] = None
        if msg_type == "audio" and file_key and self._audio_transcriber is not None:
            try:
                async def _download(target_path: Path) -> None:
                    await self._download_message_resource_to_path(
                        message_id=message_id,
                        file_key=file_key,
                        target_path=target_path,
                        msg_type=msg_type,
                    )

                transcript = await self._audio_transcriber.transcribe_via_downloader(
                    source_id=f"feishu_{message_id}_{file_key}",
                    downloader=_download,
                    mime_type=mime_type,
                    filename=f"{file_key}.ogg",
                )
            except AudioTranscriptionError as exc:
                stt_error = str(exc)
                logger.warning("Feishu voice transcription unavailable: %s", exc)
            except Exception as exc:
                stt_error = f"Unexpected Feishu STT error: {exc}"
                logger.exception("Unexpected Feishu voice transcription failure")

        final_content = text
        if transcript:
            if final_content:
                final_content = f"{final_content}\n\n[Feishu voice transcript]\n{transcript}"
            else:
                final_content = f"[Feishu voice message transcribed]\n{transcript}"
        elif not final_content and msg_type == "audio":
            final_content = (
                "[Feishu voice message received]\n"
                "Built-in speech-to-text could not transcribe this message."
            )
            if stt_error:
                final_content += f"\nstt_error={stt_error}"
        elif not final_content:
            return
        
        chat_id = message_data.get("chat_id", "")
        user_id = sender.get("open_id", sender.get("user_id", ""))
        
        message = self._create_message(
            message_id=message_id,
            sender_id=user_id,
            sender_name=user_id,  # Feishu doesn't always return name in events
            content=final_content,
            chat_id=chat_id,
            feishu_message_type=msg_type or "text",
            feishu_voice_file_key=file_key,
            feishu_voice_duration=duration,
            feishu_voice_mime_type=mime_type,
            feishu_voice_transcript=transcript,
            feishu_voice_stt_error=stt_error,
        )
        message.metadata["_chat_id"] = chat_id
        
        await self._message_queue.put(message)
        logger.info("Feishu message received: %s", final_content[:50])
    
    async def _handle_card_action(self, event: dict[str, Any]) -> None:
        """Handle Interactive Card button click (auth response)."""
        action = event.get("action", {})
        value = action.get("value", {})
        operator = event.get("operator", {})
        
        auth_action = value.get("action")
        auth_request_id = value.get("auth_request_id")
        
        if not auth_action or not auth_request_id:
            return
        
        user_id = operator.get("open_id", "")
        approved = auth_action == "approve"
        
        # Verify admin
        if user_id not in self.admin_user_ids:
            logger.warning(f"Unauthorized Feishu auth attempt from {user_id}")
            return
        
        auth_response = AuthorizationResponse(
            auth_request_id=auth_request_id,
            user_id=user_id,
            approved=approved,
        )
        
        future = self._auth_futures.get(auth_request_id)
        if future and not future.done():
            future.set_result(auth_response)
        
        logger.info(f"Feishu auth response: {auth_request_id} -> {'approved' if approved else 'rejected'}")


