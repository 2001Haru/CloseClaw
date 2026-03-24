"""Tests for heartbeat service (Phase 6 S1)."""

from __future__ import annotations

import asyncio

import pytest

from closeclaw.heartbeat.service import HeartbeatService
from closeclaw.heartbeat.types import HeartbeatDecision
from closeclaw.memory.workspace_layout import ensure_workspace_memory_layout


@pytest.mark.asyncio
async def test_start_is_idempotent(tmp_path):
    service = HeartbeatService(workspace_root=str(tmp_path), enabled=True, interval_s=3600)

    await service.start()
    first_task = service._task
    await service.start()

    assert service._running is True
    assert service._task is first_task

    await service.stop()


@pytest.mark.asyncio
async def test_trigger_now_skips_when_file_missing(tmp_path):
    service = HeartbeatService(workspace_root=str(tmp_path), enabled=True)

    result = await service.trigger_now()

    assert result.action == "skip"
    assert result.status == "skipped"
    assert result.reason == "empty_or_missing_heartbeat_file"


@pytest.mark.asyncio
async def test_trigger_now_runs_with_default_decision(tmp_path):
    ensure_workspace_memory_layout(str(tmp_path))
    (tmp_path / "CloseClaw Memory" / "HEARTBEAT.md").write_text("Check pending reminders", encoding="utf-8")

    async def on_execute(tasks: str):
        return {"ok": True, "tasks": tasks}

    service = HeartbeatService(
        workspace_root=str(tmp_path),
        enabled=True,
        on_execute=on_execute,
    )

    result = await service.trigger_now()

    assert result.action == "run"
    assert result.status == "completed"
    assert result.result["ok"] is True
    assert "pending reminders" in result.result["tasks"]


@pytest.mark.asyncio
async def test_decision_fn_skip_path(tmp_path):
    ensure_workspace_memory_layout(str(tmp_path))
    (tmp_path / "CloseClaw Memory" / "HEARTBEAT.md").write_text("noop", encoding="utf-8")

    async def decide(_content: str) -> HeartbeatDecision:
        return HeartbeatDecision(action="skip", reason="manual_skip")

    service = HeartbeatService(
        workspace_root=str(tmp_path),
        enabled=True,
        decision_fn=decide,
    )

    result = await service.trigger_now()

    assert result.action == "skip"
    assert result.reason == "manual_skip"


@pytest.mark.asyncio
async def test_invalid_decision_falls_back_to_skip(tmp_path):
    ensure_workspace_memory_layout(str(tmp_path))
    (tmp_path / "CloseClaw Memory" / "HEARTBEAT.md").write_text("run", encoding="utf-8")

    async def bad_decide(_content: str) -> HeartbeatDecision:
        return HeartbeatDecision(action="unknown")

    service = HeartbeatService(
        workspace_root=str(tmp_path),
        enabled=True,
        decision_fn=bad_decide,
    )

    result = await service.trigger_now()

    assert result.action == "skip"
    assert result.reason == "invalid_decision_action"


@pytest.mark.asyncio
async def test_loop_continues_after_tick_exception(tmp_path):
    ensure_workspace_memory_layout(str(tmp_path))
    (tmp_path / "CloseClaw Memory" / "HEARTBEAT.md").write_text("work", encoding="utf-8")

    calls = {"count": 0}

    async def on_execute(_tasks: str):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    service = HeartbeatService(
        workspace_root=str(tmp_path),
        enabled=True,
        interval_s=0,
        on_execute=on_execute,
    )

    await service.start()
    await asyncio.sleep(0.02)
    await service.stop()

    assert calls["count"] >= 2


@pytest.mark.asyncio
async def test_quiet_hours_gate_skips_tick(tmp_path):
    ensure_workspace_memory_layout(str(tmp_path))
    (tmp_path / "CloseClaw Memory" / "HEARTBEAT.md").write_text("run me", encoding="utf-8")

    service = HeartbeatService(
        workspace_root=str(tmp_path),
        enabled=True,
        quiet_hours_enabled=True,
        quiet_hours_timezone="UTC",
        quiet_hours_ranges=["00:00-23:59"],
    )

    result = await service.trigger_now()

    assert result.action == "skip"
    assert result.reason == "quiet_hours"


@pytest.mark.asyncio
async def test_queue_busy_guard_skips_tick(tmp_path):
    ensure_workspace_memory_layout(str(tmp_path))
    (tmp_path / "CloseClaw Memory" / "HEARTBEAT.md").write_text("run me", encoding="utf-8")

    service = HeartbeatService(
        workspace_root=str(tmp_path),
        enabled=True,
        queue_busy_guard_enabled=True,
        max_queue_size=10,
        queue_size_getter=lambda: 10,
    )

    result = await service.trigger_now()

    assert result.action == "skip"
    assert result.reason == "queue_busy"


@pytest.mark.asyncio
async def test_target_ttl_keeps_stable_routing(tmp_path):
    ensure_workspace_memory_layout(str(tmp_path))
    (tmp_path / "CloseClaw Memory" / "HEARTBEAT.md").write_text("run me", encoding="utf-8")

    resolve_calls = {"count": 0}

    def resolver():
        resolve_calls["count"] += 1
        if resolve_calls["count"] == 1:
            return ("telegram", "chat_a")
        return ("telegram", "chat_b")

    async def on_execute(tasks: str):
        return {"ok": True, "tasks": tasks}

    service = HeartbeatService(
        workspace_root=str(tmp_path),
        enabled=True,
        on_execute=on_execute,
        target_resolver=resolver,
        target_ttl_s=3600,
        fallback_channel="cli",
        fallback_chat_id="direct",
    )

    first = await service.trigger_now()
    second = await service.trigger_now()

    assert first.target_channel == "telegram"
    assert first.target_chat_id == "chat_a"
    assert second.target_chat_id == "chat_a"
    assert resolve_calls["count"] == 1
