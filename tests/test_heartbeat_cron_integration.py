"""Integration tests for heartbeat and cron co-running behavior."""

from __future__ import annotations

import asyncio

import pytest

from closeclaw.cron.service import CronService
from closeclaw.cron.types import CronSchedule
from closeclaw.heartbeat.service import HeartbeatService
from closeclaw.memory.workspace_layout import ensure_workspace_memory_layout


@pytest.mark.asyncio
async def test_heartbeat_and_cron_can_run_together(tmp_path):
    workspace = tmp_path
    ensure_workspace_memory_layout(str(workspace))
    (workspace / "CloseClaw Memory" / "HEARTBEAT.md").write_text("Do a periodic check", encoding="utf-8")

    heartbeat_calls: list[str] = []
    cron_calls: list[str] = []

    async def on_heartbeat(tasks: str):
        heartbeat_calls.append(tasks)
        return {"status": "ok"}

    async def on_cron(job):
        cron_calls.append(job.id)
        return {"status": "ok"}

    heartbeat = HeartbeatService(
        workspace_root=str(workspace),
        enabled=True,
        interval_s=1,
        on_execute=on_heartbeat,
    )
    cron = CronService(
        store_file=str(workspace / "cron_jobs.json"),
        enabled=True,
        on_job=on_cron,
    )
    cron.add_job("job1", CronSchedule(kind="every", every_ms=20), "hello")

    await heartbeat.start()
    await cron.start()

    # Trigger one immediate heartbeat tick while cron loop is running.
    hb_result = await heartbeat.trigger_now()
    await asyncio.sleep(0.07)

    await cron.stop()
    await heartbeat.stop()

    assert hb_result.status == "completed"
    assert len(heartbeat_calls) >= 1
    assert len(cron_calls) >= 1
