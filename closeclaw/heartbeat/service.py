"""Heartbeat service (Phase 6 S1 MVP)."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from .types import HeartbeatDecision, HeartbeatTickResult
from ..memory.workspace_layout import memory_root_dir

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Periodic HEARTBEAT.md scanner with two-stage decision and execution."""

    def __init__(
        self,
        *,
        workspace_root: str,
        enabled: bool = True,
        interval_s: int = 1800,
        decision_fn: Optional[Callable[[str], Awaitable[HeartbeatDecision]]] = None,
        on_execute: Optional[Callable[..., Awaitable[Any]]] = None,
        on_notify: Optional[Callable[[Any], Awaitable[None]]] = None,
        notify_enabled: bool = False,
        quiet_hours_enabled: bool = False,
        quiet_hours_timezone: str = "UTC",
        quiet_hours_ranges: Optional[list[str]] = None,
        queue_busy_guard_enabled: bool = False,
        max_queue_size: int = 100,
        queue_size_getter: Optional[Callable[[], int]] = None,
        target_ttl_s: int = 1800,
        fallback_channel: str = "cli",
        fallback_chat_id: str = "direct",
        target_resolver: Optional[Callable[[], tuple[str, str] | None]] = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.enabled = enabled
        self.interval_s = interval_s
        self._decision_fn = decision_fn
        self._on_execute = on_execute
        self._on_notify = on_notify
        self._notify_enabled = notify_enabled
        self._quiet_hours_enabled = quiet_hours_enabled
        self._quiet_hours_timezone = quiet_hours_timezone
        self._quiet_hours_ranges = quiet_hours_ranges or []
        self._queue_busy_guard_enabled = queue_busy_guard_enabled
        self._max_queue_size = max_queue_size
        self._queue_size_getter = queue_size_getter
        self._target_ttl_s = target_ttl_s
        self._fallback_channel = fallback_channel
        self._fallback_chat_id = fallback_chat_id
        self._target_resolver = target_resolver

        self._last_target: tuple[str, str] | None = None
        self._last_target_at: float = 0.0

        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start background heartbeat loop (idempotent)."""
        if not self.enabled:
            logger.info("Heartbeat is disabled by configuration")
            return

        if self._running:
            logger.warning("HeartbeatService already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("HeartbeatService started")

    async def stop(self) -> None:
        """Stop heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        logger.info("HeartbeatService stopped")

    async def trigger_now(self) -> HeartbeatTickResult:
        """Run one heartbeat tick immediately."""
        return await self._tick()

    async def _run_loop(self) -> None:
        """Periodic heartbeat scheduler loop."""
        try:
            while self._running:
                await asyncio.sleep(self.interval_s)
                try:
                    await self._tick()
                except Exception as exc:
                    logger.error("Heartbeat execution failed: %s", exc, exc_info=True)
        except asyncio.CancelledError:
            logger.debug("HeartbeatService loop cancelled")
            raise

    async def _tick(self) -> HeartbeatTickResult:
        """Run a single heartbeat tick."""
        tick_started = time.time()
        logger.info("heartbeat.tick_started")

        if self._quiet_hours_hit():
            result = HeartbeatTickResult(
                action="skip",
                status="skipped",
                reason="quiet_hours",
                duration_ms=int((time.time() - tick_started) * 1000),
            )
            logger.info("heartbeat.tick_skipped reason=%s", result.reason)
            return result

        if self._queue_busy_hit():
            result = HeartbeatTickResult(
                action="skip",
                status="skipped",
                reason="queue_busy",
                duration_ms=int((time.time() - tick_started) * 1000),
            )
            logger.info("heartbeat.tick_skipped reason=%s", result.reason)
            return result

        content = self._read_heartbeat_file()
        if not content:
            return HeartbeatTickResult(
                action="skip",
                status="skipped",
                reason="empty_or_missing_heartbeat_file",
                duration_ms=int((time.time() - tick_started) * 1000),
            )

        decision = await self._decide(content)
        if decision.action != "run":
            return HeartbeatTickResult(
                action="skip",
                tasks=decision.tasks,
                status="skipped",
                reason=decision.reason or "decision_skip",
                duration_ms=int((time.time() - tick_started) * 1000),
            )

        if not self._on_execute:
            return HeartbeatTickResult(
                action="run",
                tasks=decision.tasks,
                status="skipped",
                reason="missing_execute_callback",
                duration_ms=int((time.time() - tick_started) * 1000),
            )

        target_channel, target_chat_id = self._resolve_target()
        logger.info(
            "heartbeat.tick_run_started target_channel=%s target_chat_id=%s",
            target_channel,
            target_chat_id,
        )
        exec_result = await self._invoke_execute(
            decision.tasks,
            target_channel=target_channel,
            target_chat_id=target_chat_id,
        )
        if self._notify_enabled and self._on_notify and exec_result is not None:
            await self._on_notify(exec_result)

        result = HeartbeatTickResult(
            action="run",
            tasks=decision.tasks,
            status="completed",
            result=exec_result,
            target_channel=target_channel,
            target_chat_id=target_chat_id,
            duration_ms=int((time.time() - tick_started) * 1000),
        )
        logger.info(
            "heartbeat.tick_run_finished status=%s duration_ms=%s",
            result.status,
            result.duration_ms,
        )
        return result

    async def _invoke_execute(
        self,
        tasks: str,
        *,
        target_channel: str,
        target_chat_id: str,
    ) -> Any:
        """Execute heartbeat callback with backward-compatible signature handling."""
        if not self._on_execute:
            return None

        try:
            signature = inspect.signature(self._on_execute)
            accepts_var_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in signature.parameters.values()
            )
            if accepts_var_kwargs or "target_channel" in signature.parameters or "target_chat_id" in signature.parameters:
                return await self._on_execute(
                    tasks,
                    target_channel=target_channel,
                    target_chat_id=target_chat_id,
                )
        except (TypeError, ValueError):
            # Fallback for callables without inspectable signature.
            pass

        return await self._on_execute(tasks)

    def _read_heartbeat_file(self) -> str:
        """Read HEARTBEAT.md from CloseClaw Memory root."""
        path = Path(memory_root_dir(self.workspace_root)) / "HEARTBEAT.md"
        if not path.exists():
            return ""

        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.error("Failed to read HEARTBEAT.md: %s", exc)
            return ""

    async def _decide(self, content: str) -> HeartbeatDecision:
        """Decide whether to skip or run based on content."""
        if self._decision_fn:
            decision = await self._decision_fn(content)
            if decision.action not in ("skip", "run"):
                return HeartbeatDecision(action="skip", reason="invalid_decision_action")
            return decision

        # S1 MVP fallback: non-empty content means run.
        return HeartbeatDecision(action="run", tasks=content)

    def _quiet_hours_hit(self) -> bool:
        if not self._quiet_hours_enabled or not self._quiet_hours_ranges:
            return False

        if self._quiet_hours_timezone.upper() == "UTC":
            tz = timezone.utc
        else:
            try:
                tz = ZoneInfo(self._quiet_hours_timezone)
            except Exception:
                logger.warning("Invalid quiet-hours timezone '%s'; ignoring quiet-hours gate", self._quiet_hours_timezone)
                return False

        now_local = datetime.now(tz)
        now_minutes = now_local.hour * 60 + now_local.minute

        for raw_range in self._quiet_hours_ranges:
            if "-" not in raw_range:
                continue
            start_raw, end_raw = raw_range.split("-", 1)
            try:
                sh, sm = [int(x) for x in start_raw.split(":", 1)]
                eh, em = [int(x) for x in end_raw.split(":", 1)]
            except Exception:
                continue

            start_minutes = sh * 60 + sm
            end_minutes = eh * 60 + em

            if start_minutes <= end_minutes:
                if start_minutes <= now_minutes <= end_minutes:
                    return True
            else:
                if now_minutes >= start_minutes or now_minutes <= end_minutes:
                    return True

        return False

    def _queue_busy_hit(self) -> bool:
        if not self._queue_busy_guard_enabled or not self._queue_size_getter:
            return False
        try:
            return self._queue_size_getter() >= self._max_queue_size
        except Exception:
            return False

    def _resolve_target(self) -> tuple[str, str]:
        now_ts = time.time()
        if self._last_target and self._target_ttl_s > 0 and (now_ts - self._last_target_at) <= self._target_ttl_s:
            return self._last_target

        resolved: tuple[str, str] | None = None
        if self._target_resolver:
            try:
                resolved = self._target_resolver()
            except Exception:
                resolved = None

        if not resolved:
            resolved = (self._fallback_channel, self._fallback_chat_id)

        self._last_target = resolved
        self._last_target_at = now_ts
        return resolved
