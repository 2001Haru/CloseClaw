"""Types for cron scheduling and persisted jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional
import time


ScheduleKind = Literal["at", "every", "cron"]


@dataclass
class CronSchedule:
    kind: ScheduleKind
    at_ms: Optional[int] = None
    every_ms: Optional[int] = None
    expr: Optional[str] = None
    tz: str = "UTC"


@dataclass
class CronJobState:
    next_run_at_ms: Optional[int] = None
    last_run_at_ms: Optional[int] = None
    last_status: str = "never"
    last_error: Optional[str] = None


@dataclass
class CronJob:
    id: str
    enabled: bool
    schedule: CronSchedule
    message: str
    deliver: bool = False
    channel: str = "cli"
    to: str = "direct"
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    state: CronJobState = field(default_factory=CronJobState)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "enabled": self.enabled,
            "schedule": {
                "kind": self.schedule.kind,
                "at_ms": self.schedule.at_ms,
                "every_ms": self.schedule.every_ms,
                "expr": self.schedule.expr,
                "tz": self.schedule.tz,
            },
            "message": self.message,
            "deliver": self.deliver,
            "channel": self.channel,
            "to": self.to,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "state": {
                "next_run_at_ms": self.state.next_run_at_ms,
                "last_run_at_ms": self.state.last_run_at_ms,
                "last_status": self.state.last_status,
                "last_error": self.state.last_error,
            },
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "CronJob":
        s = payload.get("schedule", {})
        st = payload.get("state", {})
        return CronJob(
            id=str(payload.get("id")),
            enabled=bool(payload.get("enabled", True)),
            schedule=CronSchedule(
                kind=s.get("kind", "every"),
                at_ms=s.get("at_ms"),
                every_ms=s.get("every_ms"),
                expr=s.get("expr"),
                tz=s.get("tz", "UTC"),
            ),
            message=str(payload.get("message", "")),
            deliver=bool(payload.get("deliver", False)),
            channel=str(payload.get("channel", "cli")),
            to=str(payload.get("to", "direct")),
            created_at_ms=int(payload.get("created_at_ms", 0) or 0),
            updated_at_ms=int(payload.get("updated_at_ms", 0) or 0),
            state=CronJobState(
                next_run_at_ms=st.get("next_run_at_ms"),
                last_run_at_ms=st.get("last_run_at_ms"),
                last_status=st.get("last_status", "never"),
                last_error=st.get("last_error"),
            ),
        )
