"""Cron subsystem (Phase 6)."""

from typing import Optional

from .service import CronService
from .types import CronJob, CronSchedule

_runtime_cron_service: Optional[CronService] = None


def set_runtime_cron_service(service: Optional[CronService]) -> None:
	"""Register/unregister the process-wide runtime CronService instance."""
	global _runtime_cron_service
	_runtime_cron_service = service


def get_runtime_cron_service() -> Optional[CronService]:
	"""Return the active runtime CronService instance if available."""
	return _runtime_cron_service


__all__ = [
	"CronService",
	"CronJob",
	"CronSchedule",
	"set_runtime_cron_service",
	"get_runtime_cron_service",
]
