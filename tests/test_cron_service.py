"""Tests for cron service baseline (Phase 6 S5/S6)."""

from __future__ import annotations

import asyncio
import time

import pytest

from closeclaw.cron.service import CronService
from closeclaw.cron.types import CronSchedule


@pytest.mark.asyncio
async def test_add_and_list_job(tmp_path):
    service = CronService(store_file=str(tmp_path / "cron.json"), enabled=True)

    service.add_job("job1", CronSchedule(kind="every", every_ms=1000), "hello")
    jobs = service.list_jobs()

    assert len(jobs) == 1
    assert jobs[0].id == "job1"


@pytest.mark.asyncio
async def test_run_now_invokes_callback(tmp_path):
    calls = []

    async def on_job(job):
        calls.append(job.id)
        return {"ok": True}

    service = CronService(store_file=str(tmp_path / "cron.json"), enabled=True, on_job=on_job)
    service.add_job("job1", CronSchedule(kind="every", every_ms=1000), "hello")

    result = await service.run_now("job1")

    assert calls == ["job1"]
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_timezone_validation_for_cron_kind(tmp_path):
    service = CronService(store_file=str(tmp_path / "cron.json"), enabled=True)

    with pytest.raises(Exception):
        service.add_job("job1", CronSchedule(kind="cron", expr="*/1 * * * *", tz="Bad/TZ"), "hello")


@pytest.mark.asyncio
async def test_recursion_guard_blocks_add_inside_job(tmp_path):
    holder = {"service": None}

    async def on_job(_job):
        holder["service"].add_job("nested", CronSchedule(kind="every", every_ms=1000), "x")

    service = CronService(store_file=str(tmp_path / "cron.json"), enabled=True, on_job=on_job)
    holder["service"] = service
    service.add_job("job1", CronSchedule(kind="every", every_ms=1000), "hello")

    with pytest.raises(ValueError):
        await service.run_now("job1")


@pytest.mark.asyncio
async def test_start_stop_and_due_execution(tmp_path):
    calls = []

    async def on_job(job):
        calls.append(job.id)

    service = CronService(store_file=str(tmp_path / "cron.json"), enabled=True, on_job=on_job)
    service.add_job("job1", CronSchedule(kind="every", every_ms=20), "hello")

    await service.start()
    await asyncio.sleep(0.06)
    await service.stop()

    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_persistence_is_loaded_without_start(tmp_path):
    store_file = tmp_path / "cron.json"

    writer = CronService(store_file=str(store_file), enabled=True)
    writer.add_job("job1", CronSchedule(kind="every", every_ms=1000), "hello")

    reader = CronService(store_file=str(store_file), enabled=True)
    jobs = reader.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "job1"

    calls = []

    async def on_job(job):
        calls.append(job.id)
        return {"ok": True}

    runner = CronService(store_file=str(store_file), enabled=True, on_job=on_job)
    result = await runner.run_now("job1")

    assert calls == ["job1"]
    assert result["ok"] is True
