"""Progress assessment primitives for Phase5 P3."""

from dataclasses import dataclass


@dataclass
class ProgressSnapshot:
    """Structured progress signal produced from a step transition."""

    progress_made: bool
    stagnation_count: int
    replan_required: bool


def assess_progress(previous_stagnation: int, tool_status: str, no_progress_limit: int) -> ProgressSnapshot:
    """Compute progress signal from latest tool status.

    Success resets stagnation. Non-success statuses increment stagnation.
    """
    if tool_status == "success":
        stagnation = 0
    else:
        stagnation = max(0, previous_stagnation) + 1

    return ProgressSnapshot(
        progress_made=(tool_status == "success"),
        stagnation_count=stagnation,
        replan_required=(stagnation >= no_progress_limit),
    )
