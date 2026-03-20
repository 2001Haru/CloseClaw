"""Tests for RuntimeLoopService extraction."""

import asyncio
from types import SimpleNamespace

import pytest

from closeclaw.services.runtime_loop_service import RuntimeLoopService


@pytest.mark.asyncio
async def test_emit_task_completed_payload_shape():
    service = RuntimeLoopService()
    calls = []

    async def output_fn(payload):
        calls.append(payload)

    await service.emit_task_completed(
        output_fn,
        {"task_id": "#001", "status": "completed", "result": {"ok": True}, "error": None},
    )

    assert calls[0]["type"] == "task_completed"
    assert calls[0]["task_id"] == "#001"


@pytest.mark.asyncio
async def test_emit_response_and_error_payload_shape():
    service = RuntimeLoopService()
    calls = []

    async def output_fn(payload):
        calls.append(payload)

    await service.emit_response(output_fn, response="ok", tool_calls=[], tool_results=[])
    await service.emit_error(output_fn, error="boom")

    assert calls[0]["type"] == "response"
    assert calls[0]["response"] == "ok"
    assert calls[1]["type"] == "error"
    assert "boom" in calls[1]["error"]


@pytest.mark.asyncio
async def test_await_auth_or_message_prefers_new_message_when_first():
    service = RuntimeLoopService()

    async def auth_response_fn(auth_request_id, timeout):
        await asyncio.sleep(0.05)
        return SimpleNamespace(approved=True, user_id="admin")

    async def message_input_fn():
        return {"type": "user", "content": "interrupt"}

    result = await service.await_auth_or_message(
        auth_response_fn=auth_response_fn,
        message_input_fn=message_input_fn,
        auth_request_id="auth_1",
        timeout_seconds=300.0,
    )

    assert result["kind"] == "new_message"
    assert result["message"]["content"] == "interrupt"


@pytest.mark.asyncio
async def test_await_auth_or_message_returns_auth_response_when_first():
    service = RuntimeLoopService()

    async def auth_response_fn(auth_request_id, timeout):
        return SimpleNamespace(approved=False, user_id="reviewer")

    async def message_input_fn():
        await asyncio.sleep(0.05)
        return {"type": "user", "content": "later"}

    result = await service.await_auth_or_message(
        auth_response_fn=auth_response_fn,
        message_input_fn=message_input_fn,
        auth_request_id="auth_2",
        timeout_seconds=300.0,
    )

    assert result["kind"] == "auth_response"
    assert result["auth_response"].approved is False


def test_append_system_notice_appends_message_with_suffix():
    service = RuntimeLoopService()
    history = []

    service.append_system_notice(
        message_history=history,
        channel_type="cli",
        suffix="auth_ok",
        content="[System] approved",
    )

    assert len(history) == 1
    assert history[0].sender_id == "system"
    assert history[0].channel_type == "cli"
    assert history[0].content == "[System] approved"
    assert history[0].id.endswith("_auth_ok")


@pytest.mark.asyncio
async def test_emit_resume_payload_sends_assistant_and_followup_auth_request():
    service = RuntimeLoopService()
    calls = []

    async def output_fn(payload):
        calls.append(payload)

    payload = {
        "response": "continue",
        "tool_calls": [],
        "tool_results": [],
        "requires_auth": True,
        "auth_request_id": "auth_2",
        "pending_auth": {
            "tool_name": "delete_file",
            "description": "Delete file",
            "diff_preview": "- test.txt",
        },
    }

    await service.emit_resume_payload(output_fn, resume_payload=payload)

    assert len(calls) == 2
    assert calls[0]["type"] == "assistant_message"
    assert calls[1]["type"] == "auth_request"
    assert calls[1]["auth_request_id"] == "auth_2"


@pytest.mark.asyncio
async def test_emit_auth_approved_resolution_with_resume_payload():
    service = RuntimeLoopService()
    calls = []
    history = []

    async def output_fn(payload):
        calls.append(payload)

    await service.emit_auth_approved_resolution(
        output_fn,
        message_history=history,
        channel_type="cli",
        auth_result={"status": "approved", "result": "ok"},
        resume_payload={"response": "next", "tool_calls": [], "tool_results": []},
    )

    assert len(history) == 1
    assert history[0].id.endswith("_auth_ok")
    assert calls[0]["type"] == "assistant_message"


@pytest.mark.asyncio
async def test_emit_auth_rejected_and_timeout_resolution_shapes():
    service = RuntimeLoopService()
    calls = []
    history = []

    async def output_fn(payload):
        calls.append(payload)

    await service.emit_auth_rejected_resolution(
        output_fn,
        message_history=history,
        channel_type="cli",
        auth_result={"status": "rejected", "error": "deny"},
    )
    await service.emit_auth_timeout_resolution(
        output_fn,
        message_history=history,
        channel_type="cli",
    )

    assert history[0].id.endswith("_auth_fail")
    assert history[1].id.endswith("_auth_timeout")
    assert calls[0]["type"] == "response"
    assert "deny" in calls[0]["response"]
    assert calls[1]["type"] == "response"
    assert "timed out" in calls[1]["response"].lower()


@pytest.mark.asyncio
async def test_emit_auth_interrupted_resolution_with_new_response():
    service = RuntimeLoopService()
    calls = []
    history = []

    async def output_fn(payload):
        calls.append(payload)

    await service.emit_auth_interrupted_resolution(
        output_fn,
        message_history=history,
        channel_type="cli",
        new_response={"response": "processed", "tool_calls": [], "tool_results": []},
    )

    assert len(history) == 1
    assert history[0].id.endswith("_cancel")
    assert calls[0]["type"] == "response"
    assert "Auth cancelled" in calls[0]["response"]
    assert calls[1]["type"] == "response"
    assert calls[1]["response"] == "processed"


@pytest.mark.asyncio
async def test_emit_auth_response_resolution_dispatches_approved_path():
    service = RuntimeLoopService()
    calls = []
    history = []

    async def output_fn(payload):
        calls.append(payload)

    await service.emit_auth_response_resolution(
        output_fn,
        message_history=history,
        channel_type="cli",
        approved=True,
        auth_result={"status": "approved", "result": "ok"},
        resume_payload=None,
    )

    assert len(history) == 1
    assert history[0].id.endswith("_auth_ok")
    assert calls[0]["type"] == "response"
    assert "Operation approved" in calls[0]["response"]


@pytest.mark.asyncio
async def test_emit_auth_response_resolution_dispatches_rejected_path():
    service = RuntimeLoopService()
    calls = []
    history = []

    async def output_fn(payload):
        calls.append(payload)

    await service.emit_auth_response_resolution(
        output_fn,
        message_history=history,
        channel_type="cli",
        approved=True,
        auth_result={"status": "denied", "error": "no"},
        resume_payload=None,
    )

    assert len(history) == 1
    assert history[0].id.endswith("_auth_fail")
    assert calls[0]["type"] == "response"
    assert "Operation denied" in calls[0]["response"]


@pytest.mark.asyncio
async def test_handle_auth_interruption_processes_and_emits():
    service = RuntimeLoopService()
    calls = []
    history = []

    async def output_fn(payload):
        calls.append(payload)

    async def process_message_fn(message):
        assert message["content"] == "new input"
        return {"response": "processed", "tool_calls": [], "tool_results": []}

    new_response = await service.handle_auth_interruption(
        output_fn,
        message_history=history,
        channel_type="cli",
        interrupt_message={"content": "new input"},
        process_message_fn=process_message_fn,
    )

    assert new_response and new_response["response"] == "processed"
    assert len(history) == 1
    assert history[0].id.endswith("_cancel")
    assert calls[0]["type"] == "response"
    assert calls[1]["type"] == "response"


@pytest.mark.asyncio
async def test_handle_auth_interruption_with_empty_message_still_emits_cancel():
    service = RuntimeLoopService()
    calls = []
    history = []

    async def output_fn(payload):
        calls.append(payload)

    async def process_message_fn(_):
        raise AssertionError("process_message_fn should not be called when interrupt message is empty")

    new_response = await service.handle_auth_interruption(
        output_fn,
        message_history=history,
        channel_type="cli",
        interrupt_message=None,
        process_message_fn=process_message_fn,
    )

    assert new_response is None
    assert len(history) == 1
    assert history[0].id.endswith("_cancel")
    assert len(calls) == 1
    assert "Auth cancelled" in calls[0]["response"]
