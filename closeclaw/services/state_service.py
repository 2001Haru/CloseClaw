"""State persistence service for AgentCore runtime snapshots."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..types import Message

logger = logging.getLogger(__name__)


class StateService:
    """Handles state snapshot creation, disk persistence, and restoration helpers."""

    def __init__(
        self,
        workspace_root_getter: Callable[[], str],
        state_file_getter: Callable[[], Optional[str]],
        task_manager_getter: Callable[[], Any],
    ) -> None:
        self._workspace_root_getter = workspace_root_getter
        self._state_file_getter = state_file_getter
        self._task_manager_getter = task_manager_getter

    async def build_state_snapshot(
        self,
        *,
        agent_state: str,
        message_history: list[Message],
        compact_memory_snapshot: Optional[str],
        pending_auth_requests: dict[str, Any],
    ) -> dict[str, Any]:
        """Build runtime state snapshot dict in the existing state.json contract."""
        state_dict: dict[str, Any] = {
            "version": "0.1",
            "agent_state": agent_state,
            "last_save_time": datetime.now(timezone.utc).isoformat(),
            "message_history": [
                msg.to_dict() if hasattr(msg, "to_dict") else str(msg)
                for msg in message_history
            ],
            "compact_memory_snapshot": compact_memory_snapshot,
            "pending_auth_requests": self.serialize_pending_auth_requests(pending_auth_requests),
        }

        task_manager = self._task_manager_getter()
        if task_manager:
            task_state = await task_manager.save_to_state()
            state_dict.update(task_state)
        else:
            state_dict["active_tasks"] = {}
            state_dict["completed_results"] = {}

        return state_dict

    def serialize_pending_auth_requests(self, pending_auth_requests: dict[str, Any]) -> dict[str, Any]:
        """Serialize pending auth requests for persistence.

        Current policy intentionally drops pending auth payloads across restarts to avoid
        replaying stale approvals with potentially invalid runtime context.
        """
        _ = pending_auth_requests
        return {}

    def restore_pending_auth_requests(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Restore persisted pending auth requests with defensive shape checks."""
        payload = state_dict.get("pending_auth_requests", {})
        if isinstance(payload, dict):
            return payload
        return {}

    def restore_compact_memory_snapshot(self, state_dict: dict[str, Any]) -> Optional[str]:
        """Restore compact memory snapshot with defensive type validation."""
        payload = state_dict.get("compact_memory_snapshot")
        if payload is None or isinstance(payload, str):
            return payload
        return None

    async def persist_state_snapshot(self, state_dict: dict[str, Any], message_count: int) -> None:
        """Persist state snapshot to disk using atomic temp-file replace strategy."""
        state_file = self._state_file_getter()
        if not state_file:
            logger.warning("[DEBUG] _save_state: state_file not set! state_file=N/A")
            return

        root = self._workspace_root_getter() or "."
        path = os.path.join(root, state_file)

        try:
            temp_path = f"{path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state_dict, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
            logger.info("[DEBUG] _save_state: saved %s messages to %s", message_count, path)
        except Exception as exc:
            logger.error("Failed to persist state to %s: %s", path, exc)

    async def load_state_dict_from_disk(self) -> Optional[dict[str, Any]]:
        """Load persisted state dict from disk if configured and present."""
        state_file = self._state_file_getter()
        workspace_root = self._workspace_root_getter()
        logger.info(
            "[DEBUG] load_state_from_disk: state_file=%s, workspace_root=%s",
            state_file,
            workspace_root,
        )

        if not state_file:
            logger.warning("[DEBUG] load_state_from_disk: NO state_file configured, skipping")
            return None

        root = workspace_root or "."
        path = os.path.join(root, state_file)
        if not os.path.exists(path):
            logger.info("[DEBUG] load_state_from_disk: file %s does not exist", path)
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                state_dict = json.load(f)
            logger.info(
                "[DEBUG] load_state_from_disk: loaded JSON from %s, message_history has %s entries",
                path,
                len(state_dict.get("message_history", [])),
            )
            return state_dict
        except Exception as exc:
            logger.error("Failed to load state from %s: %s", path, exc)
            return None

    def deserialize_message_history(self, state_dict: dict[str, Any]) -> Optional[list[Message]]:
        """Deserialize message history if present in persisted state."""
        if "message_history" not in state_dict:
            return None

        try:
            history: list[Message] = []
            for msg_data in state_dict["message_history"]:
                if isinstance(msg_data, dict):
                    history.append(Message.from_dict(msg_data))
            logger.info("Restored %s messages from state", len(history))
            return history
        except Exception as exc:
            logger.error("Error restoring message history: %s", exc)
            return None

    async def restore_task_manager_state(self, state_dict: dict[str, Any]) -> None:
        """Restore TaskManager runtime state if a manager is configured."""
        task_manager = self._task_manager_getter()
        if task_manager:
            await task_manager.load_from_state(state_dict)
            logger.info("Restored TaskManager active tasks from state")
