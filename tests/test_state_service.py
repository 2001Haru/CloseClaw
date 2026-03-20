"""Tests for StateService extraction."""

import pytest

from closeclaw.services.state_service import StateService
from closeclaw.types import Message


class _DummyTaskManager:
    def __init__(self):
        self.loaded = None

    async def save_to_state(self):
        return {"active_tasks": {"#001": {"status": "running"}}, "completed_results": {}}

    async def load_from_state(self, state_dict):
        self.loaded = state_dict


def test_deserialize_message_history_when_present():
    service = StateService(
        workspace_root_getter=lambda: ".",
        state_file_getter=lambda: None,
        task_manager_getter=lambda: None,
    )

    state_dict = {
        "message_history": [
            {
                "id": "m1",
                "channel_type": "cli",
                "sender_id": "u",
                "sender_name": "User",
                "content": "hello",
                "timestamp": "2026-03-20T00:00:00+00:00",
            }
        ]
    }

    history = service.deserialize_message_history(state_dict)

    assert history is not None
    assert len(history) == 1
    assert isinstance(history[0], Message)


@pytest.mark.asyncio
async def test_build_state_snapshot_includes_task_manager_state():
    tm = _DummyTaskManager()
    service = StateService(
        workspace_root_getter=lambda: ".",
        state_file_getter=lambda: None,
        task_manager_getter=lambda: tm,
    )

    state = await service.build_state_snapshot(
        agent_state="running",
        message_history=[],
        compact_memory_snapshot=None,
        pending_auth_requests={"auth_1": {"tool_name": "delete_file"}},
    )

    assert state["agent_state"] == "running"
    assert "active_tasks" in state
    assert "completed_results" in state
    assert state["pending_auth_requests"] == {}


@pytest.mark.asyncio
async def test_load_and_persist_state_snapshot_roundtrip(tmp_path):
    state_file = "state.json"
    service = StateService(
        workspace_root_getter=lambda: str(tmp_path),
        state_file_getter=lambda: state_file,
        task_manager_getter=lambda: None,
    )

    payload = {"version": "0.1", "message_history": [], "pending_auth_requests": {}}
    await service.persist_state_snapshot(payload, message_count=0)

    loaded = await service.load_state_dict_from_disk()
    assert loaded is not None
    assert loaded["version"] == "0.1"


def test_restore_pending_auth_requests_defensive_shape():
    service = StateService(
        workspace_root_getter=lambda: ".",
        state_file_getter=lambda: None,
        task_manager_getter=lambda: None,
    )

    restored_ok = service.restore_pending_auth_requests(
        {"pending_auth_requests": {"auth_1": {"tool_name": "shell"}}}
    )
    restored_bad = service.restore_pending_auth_requests(
        {"pending_auth_requests": ["invalid"]}
    )

    assert "auth_1" in restored_ok
    assert restored_bad == {}


def test_restore_compact_memory_snapshot_defensive_shape():
    service = StateService(
        workspace_root_getter=lambda: ".",
        state_file_getter=lambda: None,
        task_manager_getter=lambda: None,
    )

    good = service.restore_compact_memory_snapshot({"compact_memory_snapshot": "memo"})
    none_val = service.restore_compact_memory_snapshot({})
    bad = service.restore_compact_memory_snapshot({"compact_memory_snapshot": ["invalid"]})

    assert good == "memo"
    assert none_val is None
    assert bad is None
