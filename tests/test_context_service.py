"""Tests for ContextService extraction layer."""

import pytest

from closeclaw.services.context_service import ContextService
from closeclaw.types import Message, ToolCall, ToolResult


class _DummyContextManager:
    def count_message_tokens(self, messages):
        return 100

    def check_thresholds(self, token_count):
        return "WARNING", True

    def get_usage_ratio(self, token_count):
        return 0.8

    @property
    def max_tokens(self):
        return 1000

    def get_status_report(self, token_count):
        return {"usage_percentage": "10.0%"}


class _DummyFlushSession:
    def should_trigger_flush(self, status, usage_ratio):
        return status == "WARNING"


class _DummyFlushCoordinator:
    def __init__(self):
        self.pending_flush = False
        self.last_flush_session_id = None

    def generate_session_id(self):
        return "flush_123"


@pytest.mark.asyncio
async def test_maybe_trigger_flush_sets_pending_session():
    service = ContextService(
        context_manager=_DummyContextManager(),
        message_compactor=type("MC", (), {"active_window": 10})(),
        memory_flush_session=_DummyFlushSession(),
        memory_flush_coordinator=_DummyFlushCoordinator(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    session_id = await service.maybe_trigger_memory_flush_before_planning([
        {"role": "user", "content": "hello"}
    ])

    assert session_id == "flush_123"
    assert service.memory_flush_coordinator.pending_flush is True
    assert service.memory_flush_coordinator.last_flush_session_id == "flush_123"


def test_compact_memory_helpers_extract_normalize_and_pair():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    block = service.extract_compact_memory_block(
        "x [COMPACT_MEMORY_BLOCK]\nA\nB\n[/COMPACT_MEMORY_BLOCK] y"
    )
    assert block == "A\nB"

    normalized = service.normalize_compact_memory("A\n\n\nB")
    assert normalized is not None
    assert "A" in normalized and "B" in normalized

    pair = service.build_compact_memory_pair("summary")
    assert len(pair) == 2
    assert pair[1]["content"] == "summary"


def test_capture_compact_memory_snapshot_from_agent_messages():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    history = [
        Message(id="1", channel_type="cli", sender_id="u", sender_name="User", content="x"),
        Message(
            id="2",
            channel_type="cli",
            sender_id="agent",
            sender_name="Agent",
            content="[COMPACT_MEMORY_BLOCK]\nKEEP\n[/COMPACT_MEMORY_BLOCK]",
        ),
    ]

    snap = service.capture_compact_memory_snapshot(history, agent_id="agent")
    assert snap is not None
    assert "KEEP" in snap


def test_repair_transcript_injects_synthetic_result_for_orphan_call():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=type("Audit", (), {"log": lambda *args, **kwargs: None})(),
    )

    messages = [
        {"role": "system", "content": "You are helpful."},
        {
            "role": "assistant",
            "content": "Calling tool",
            "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {"name": "do_work", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "continue"},
    ]

    repaired = service.repair_transcript(messages)
    tool_messages = [m for m in repaired if m.get("role") == "tool"]

    assert len(tool_messages) == 1
    assert tool_messages[0].get("tool_call_id") == "call_001"


def test_repair_transcript_drops_orphan_tool_result():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=type("Audit", (), {"log": lambda *args, **kwargs: None})(),
    )

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "tool", "tool_call_id": "orphan", "content": "result"},
        {"role": "user", "content": "next"},
    ]

    repaired = service.repair_transcript(messages)
    assert not any(m.get("tool_call_id") == "orphan" for m in repaired)


def test_serialize_tool_result_covers_success_auth_and_error():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    success = ToolResult(tool_call_id="t1", status="success", result={"ok": True})
    auth = ToolResult(tool_call_id="t2", status="auth_required", result=None)
    error = ToolResult(tool_call_id="t3", status="error", result=None, error="boom")

    assert "ok" in service.serialize_tool_result(success)
    assert "authorization" in service.serialize_tool_result(auth).lower()
    assert "boom" in service.serialize_tool_result(error)


def test_append_formatted_history_messages_handles_tool_calls_and_truncation():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    long_result = "x" * 20
    history = [
        Message(
            id="1",
            channel_type="cli",
            sender_id="agent",
            sender_name="Agent",
            content="working",
            tool_calls=[ToolCall(tool_id="tc1", name="read_file", arguments={"path": "a.txt"})],
            tool_results=[ToolResult(tool_call_id="tc1", status="success", result=long_result)],
        ),
        Message(id="2", channel_type="cli", sender_id="u", sender_name="User", content="ok"),
    ]

    target: list[dict] = []
    service.append_formatted_history_messages(
        target_messages=target,
        message_history=history,
        agent_id="agent",
        max_result_chars=8,
    )

    assert target[0]["role"] == "assistant"
    assert "tool_calls" in target[0]
    assert target[1]["role"] == "tool"
    assert "truncated" in target[1]["content"]
    assert target[2]["role"] == "user"


def test_build_system_prompt_includes_recall_policy_and_suffix():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    prompt = service.build_system_prompt(
        base_prompt="You are precise.",
        has_retrieve_memory_tool=True,
        suffix="[CONTEXT MONITOR] demo",
    )

    assert "You are precise." in prompt
    assert "[MEMORY RECALL POLICY]" in prompt
    assert "[CONTEXT MONITOR] demo" in prompt


def test_build_context_monitor_suffix_format():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    suffix = service.build_context_monitor_suffix(
        token_count=120,
        max_tokens=1000,
        usage_percentage="12.0%",
    )

    assert "120/1000" in suffix
    assert "12.0%" in suffix


def test_analyze_context_usage_returns_expected_shape():
    service = ContextService(
        context_manager=_DummyContextManager(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    result = service.analyze_context_usage([{"role": "user", "content": "x"}])

    assert result["token_count"] == 100
    assert result["status"] == "WARNING"
    assert result["should_flush"] is True
    assert "10.0%" in result["token_usage_info"]


def test_apply_critical_trim_policy_reduces_history_and_returns_snapshot():
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=object(),
    )

    history = [
        Message(id=str(i), channel_type="cli", sender_id="u", sender_name="User", content=f"m{i}")
        for i in range(1, 26)
    ]
    result = service.apply_critical_trim_policy(
        message_history=history,
        capture_compact_memory_snapshot=lambda: "snap",
        keep_turns=10,
    )

    assert result["compact_snapshot"] == "snap"
    assert result["old_size"] == 25
    assert result["new_size"] == 20
    assert len(result["message_history"]) == 20


def test_log_context_threshold_warning_writes_audit_record():
    class _Audit:
        def __init__(self):
            self.calls = []

        def log(self, **kwargs):
            self.calls.append(kwargs)

    audit = _Audit()
    service = ContextService(
        context_manager=object(),
        message_compactor=object(),
        memory_flush_session=object(),
        memory_flush_coordinator=object(),
        memory_manager=object(),
        planning_service=object(),
        audit_logger=audit,
    )

    service.log_context_threshold_warning(
        status="WARNING",
        should_flush=True,
        context_report={"usage_percentage": "80%"},
        token_count=800,
        current_user_id="u1",
    )

    assert len(audit.calls) == 1
    assert audit.calls[0]["event_type"] == "context_threshold_warning"
    assert audit.calls[0]["status"] == "WARNING"
