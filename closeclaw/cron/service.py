"""Cron scheduling service (Phase 6 baseline)."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from .store import CronStore
from .types import CronJob, CronSchedule

logger = logging.getLogger(__name__)


class CronService:
    """Single-timer cron service with persistent JSON store."""

    def __init__(
        self,
        *,
        store_file: str,
        enabled: bool = True,
        default_timezone: str = "UTC",
        on_job: Optional[Callable[[CronJob], Awaitable[Any]]] = None,
    ) -> None:
        self.enabled = enabled
        self.default_timezone = default_timezone
        self._on_job = on_job
        self._store = CronStore(Path(store_file))

        self._jobs: dict[str, CronJob] = {}
        self._loaded = False
        self._running = False
        self._task: asyncio.Task | None = None
        self._cron_context = False

    async def start(self) -> None:
        if not self.enabled:
            logger.info("CronService is disabled by configuration")
            return
        if self._running:
            logger.warning("CronService already running")
            return

        self._ensure_loaded()
        now_ms = int(time.time() * 1000)
        for job in self._jobs.values():
            if job.enabled:
                job.state.next_run_at_ms = self._compute_next_run(job.schedule, now_ms)
        self._store.save(self._jobs)

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("CronService started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        logger.info("CronService stopped")

    def list_jobs(self) -> list[CronJob]:
        self._ensure_loaded()
        return sorted(self._jobs.values(), key=lambda j: j.id)

    def add_job(self, job_id: str, schedule: CronSchedule, message: str, *, deliver: bool = False, channel: str = "cli", to: str = "direct") -> CronJob:
        self._ensure_loaded()
        if self._cron_context:
            raise ValueError("cannot schedule new jobs from within a cron job execution")

        self._validate_schedule_for_add(schedule)
        if job_id in self._jobs:
            raise ValueError(f"job already exists: {job_id}")

        now_ms = int(time.time() * 1000)
        job = CronJob(
            id=job_id,
            enabled=True,
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            to=to,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        job.state.next_run_at_ms = self._compute_next_run(schedule, now_ms)
        self._jobs[job_id] = job
        self._store.save(self._jobs)
        return job

    def remove_job(self, job_id: str) -> bool:
        self._ensure_loaded()
        if job_id not in self._jobs:
            return False
        self._jobs.pop(job_id, None)
        self._store.save(self._jobs)
        return True

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        self._ensure_loaded()
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.enabled = enabled
        now_ms = int(time.time() * 1000)
        job.updated_at_ms = now_ms
        job.state.next_run_at_ms = self._compute_next_run(job.schedule, now_ms) if enabled else None
        self._store.save(self._jobs)
        return True

    async def run_now(self, job_id: str) -> Any:
        self._ensure_loaded()
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"job not found: {job_id}")
        return await self._execute_job(job)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._jobs = self._store.load()
        self._loaded = True

    async def _run_loop(self) -> None:
        try:
            while self._running:
                next_wake = self._get_next_wake_ms()
                if next_wake is None:
                    await asyncio.sleep(0.2)
                    continue
                now_ms = int(time.time() * 1000)
                sleep_s = max((next_wake - now_ms) / 1000.0, 0.0)
                await asyncio.sleep(sleep_s)
                await self._run_due_jobs()
        except asyncio.CancelledError:
            raise

    async def _run_due_jobs(self) -> None:
        now_ms = int(time.time() * 1000)
        for job in self._jobs.values():
            if not job.enabled or job.state.next_run_at_ms is None:
                continue
            if job.state.next_run_at_ms > now_ms:
                continue

            try:
                await self._execute_job(job)
                job.state.last_status = "ok"
                job.state.last_error = None
            except Exception as exc:
                job.state.last_status = "error"
                job.state.last_error = str(exc)
            finally:
                job.state.last_run_at_ms = int(time.time() * 1000)
                job.updated_at_ms = job.state.last_run_at_ms
                job.state.next_run_at_ms = self._compute_next_run(job.schedule, job.state.last_run_at_ms)

        self._store.save(self._jobs)

    async def _execute_job(self, job: CronJob) -> Any:
        if not self._on_job:
            return None
        self._cron_context = True
        try:
            return await self._on_job(job)
        finally:
            self._cron_context = False

    def _get_next_wake_ms(self) -> int | None:
        candidates = [job.state.next_run_at_ms for job in self._jobs.values() if job.enabled and job.state.next_run_at_ms is not None]
        return min(candidates) if candidates else None

    def _validate_schedule_for_add(self, schedule: CronSchedule) -> None:
        if schedule.kind == "every":
            if not schedule.every_ms or schedule.every_ms <= 0:
                raise ValueError("every_ms must be > 0 for every schedule")
            return

        if schedule.kind == "at":
            if not schedule.at_ms:
                raise ValueError("at_ms is required for at schedule")
            return

        if schedule.kind == "cron":
            if not schedule.expr:
                raise ValueError("expr is required for cron schedule")
            # Validate timezone early.
            ZoneInfo(schedule.tz or self.default_timezone)
            return

        raise ValueError(f"unsupported schedule kind: {schedule.kind}")

    def _compute_next_run(self, schedule: CronSchedule, now_ms: int) -> int | None:
        if schedule.kind == "every":
            return now_ms + int(schedule.every_ms or 0)

        if schedule.kind == "at":
            at_ms = int(schedule.at_ms or 0)
            return at_ms if at_ms > now_ms else None

        if schedule.kind == "cron":
            # Minimal baseline parser for expressions like "*/N * * * *".
            expr = (schedule.expr or "").strip()
            if expr.startswith("*/") and expr.endswith(" * * * *"):
                try:
                    minutes = int(expr.split(" ", 1)[0][2:])
                except Exception as exc:
                    raise ValueError(f"invalid cron expr: {expr}") from exc
                return now_ms + minutes * 60 * 1000
            raise ValueError(f"unsupported cron expr in baseline service: {expr}")

        return None
