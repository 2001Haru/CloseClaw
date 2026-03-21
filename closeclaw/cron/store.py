"""JSON store for cron jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import CronJob


class CronStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def load(self) -> dict[str, CronJob]:
        if not self.file_path.exists():
            return {}
        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        jobs_raw = payload.get("jobs", {}) if isinstance(payload, dict) else {}
        return {job_id: CronJob.from_dict(job) for job_id, job in jobs_raw.items()}

    def save(self, jobs: dict[str, CronJob]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "version": "1",
            "jobs": {job_id: job.to_dict() for job_id, job in jobs.items()},
        }
        temp = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.file_path)
