"""P4 tests for subtask registry lifecycle and error semantics."""

import pytest

from closeclaw.orchestrator.subtask_registry import SubtaskRegistry, SubtaskRegistryError
from closeclaw.orchestrator.subtask_types import SubtaskErrorCode, SubtaskSpec, SubtaskStatus


def test_subtask_lifecycle_success_path():
    registry = SubtaskRegistry()

    handle = registry.spawn_subtask(
        parent_run_id="run_1",
        spec=SubtaskSpec(intent="collect context", input_payload={"query": "abc"}),
    )

    created = registry.wait_subtask(handle)
    assert created.status == SubtaskStatus.CREATED

    running = registry.update_status(handle.subtask_id, SubtaskStatus.RUNNING)
    assert running.status == SubtaskStatus.RUNNING

    completed = registry.update_status(
        handle.subtask_id,
        SubtaskStatus.COMPLETED,
        output={"summary": "ok"},
    )
    assert completed.status == SubtaskStatus.COMPLETED
    assert completed.result is not None
    assert completed.result.output == {"summary": "ok"}


def test_subtask_invalid_transition_rejected():
    registry = SubtaskRegistry()
    handle = registry.spawn_subtask(parent_run_id="run_1", spec=SubtaskSpec(intent="x"))

    with pytest.raises(SubtaskRegistryError) as exc:
        registry.update_status(handle.subtask_id, SubtaskStatus.COMPLETED)

    assert exc.value.code == SubtaskErrorCode.INVALID_TRANSITION


def test_subtask_cannot_transition_after_terminal():
    registry = SubtaskRegistry()
    handle = registry.spawn_subtask(parent_run_id="run_1", spec=SubtaskSpec(intent="x"))
    registry.update_status(handle.subtask_id, SubtaskStatus.RUNNING)
    registry.update_status(handle.subtask_id, SubtaskStatus.FAILED, error="boom")

    with pytest.raises(SubtaskRegistryError) as exc:
        registry.update_status(handle.subtask_id, SubtaskStatus.CANCELLED)

    assert exc.value.code == SubtaskErrorCode.ALREADY_TERMINAL


def test_subtask_not_found_error_code():
    registry = SubtaskRegistry()

    with pytest.raises(SubtaskRegistryError) as exc:
        registry.get_record("subtask_missing")

    assert exc.value.code == SubtaskErrorCode.NOT_FOUND


def test_cancel_subtask_sets_terminal_result():
    registry = SubtaskRegistry()
    handle = registry.spawn_subtask(parent_run_id="run_2", spec=SubtaskSpec(intent="x"))

    cancelled = registry.cancel_subtask(handle, reason="user_cancelled")
    assert cancelled.status == SubtaskStatus.CANCELLED
    assert cancelled.result is not None
    assert cancelled.result.error == "user_cancelled"




