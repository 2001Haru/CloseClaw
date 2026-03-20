"""In-memory subtask registry for Phase5 P4 interface reservation."""

from typing import Any, Optional
from uuid import uuid4

from .subtask_types import (
    SubtaskErrorCode,
    SubtaskHandle,
    SubtaskRecord,
    SubtaskResult,
    SubtaskSpec,
    SubtaskStatus,
    TERMINAL_SUBTASK_STATUSES,
    utcnow_iso,
)


class SubtaskRegistryError(Exception):
    """Raised for invalid registry operations with stable error codes."""

    def __init__(self, code: SubtaskErrorCode, message: str):
        super().__init__(message)
        self.code = code


class SubtaskRegistry:
    """Minimal in-memory lifecycle registry.

    This registry intentionally does not execute subtasks; it only stores
    spawn/wait/cancel lifecycle state for P4 interface reservation.
    """

    ALLOWED_TRANSITIONS = {
        SubtaskStatus.CREATED: {SubtaskStatus.RUNNING, SubtaskStatus.CANCELLED, SubtaskStatus.FAILED},
        SubtaskStatus.RUNNING: {SubtaskStatus.COMPLETED, SubtaskStatus.FAILED, SubtaskStatus.CANCELLED},
        SubtaskStatus.COMPLETED: set(),
        SubtaskStatus.FAILED: set(),
        SubtaskStatus.CANCELLED: set(),
    }

    def __init__(self) -> None:
        self._records: dict[str, SubtaskRecord] = {}

    def spawn_subtask(self, parent_run_id: str, spec: SubtaskSpec) -> SubtaskHandle:
        subtask_id = f"subtask_{uuid4().hex[:12]}"
        now = utcnow_iso()
        record = SubtaskRecord(
            subtask_id=subtask_id,
            parent_run_id=parent_run_id,
            intent=spec.intent,
            input_payload=spec.input_payload,
            status=SubtaskStatus.CREATED,
            created_at=now,
            updated_at=now,
            result=None,
        )
        self._records[subtask_id] = record
        return SubtaskHandle(subtask_id=subtask_id, parent_run_id=parent_run_id)

    def wait_subtask(self, handle: SubtaskHandle) -> SubtaskRecord:
        return self.get_record(handle.subtask_id)

    def cancel_subtask(self, handle: SubtaskHandle, reason: str = "cancelled_by_user") -> SubtaskRecord:
        return self.update_status(
            subtask_id=handle.subtask_id,
            new_status=SubtaskStatus.CANCELLED,
            output=None,
            error=reason,
        )

    def get_record(self, subtask_id: str) -> SubtaskRecord:
        record = self._records.get(subtask_id)
        if not record:
            raise SubtaskRegistryError(
                SubtaskErrorCode.NOT_FOUND,
                f"Subtask '{subtask_id}' was not found.",
            )
        return record

    def list_records(self, parent_run_id: Optional[str] = None) -> list[SubtaskRecord]:
        records = list(self._records.values())
        if parent_run_id is not None:
            records = [r for r in records if r.parent_run_id == parent_run_id]
        return sorted(records, key=lambda r: r.created_at)

    def update_status(
        self,
        subtask_id: str,
        new_status: SubtaskStatus,
        output: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> SubtaskRecord:
        record = self.get_record(subtask_id)

        if record.status in TERMINAL_SUBTASK_STATUSES:
            raise SubtaskRegistryError(
                SubtaskErrorCode.ALREADY_TERMINAL,
                f"Subtask '{subtask_id}' is already terminal ({record.status.value}).",
            )

        allowed = self.ALLOWED_TRANSITIONS.get(record.status, set())
        if new_status not in allowed:
            raise SubtaskRegistryError(
                SubtaskErrorCode.INVALID_TRANSITION,
                f"Invalid transition: {record.status.value} -> {new_status.value}",
            )

        record.status = new_status
        record.updated_at = utcnow_iso()

        if new_status in TERMINAL_SUBTASK_STATUSES:
            record.result = SubtaskResult(status=new_status, output=output, error=error)

        return record

