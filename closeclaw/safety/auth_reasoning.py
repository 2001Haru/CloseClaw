"""Reason generation helpers for authorization requests."""

from __future__ import annotations

from typing import Any


def build_auth_reason(
    *,
    tool_name: str,
    tool_description: str,
    arguments: dict[str, Any],
    diff_preview: str | None,
) -> str:
    """Generate a concise, human-readable reason for why auth is requested."""
    path = arguments.get("path")
    operation = arguments.get("operation")

    focus = []
    if operation:
        focus.append(f"operation={operation}")
    if isinstance(path, str) and path:
        focus.append(f"path={path}")

    if diff_preview:
        diff_hint = "diff preview is available for review"
    else:
        diff_hint = "no diff preview is available"

    summary = f"Tool '{tool_name}' requests a sensitive action"
    if focus:
        summary += f" ({', '.join(focus)})."
    else:
        summary += "."

    description = (tool_description or "").strip()
    if description:
        return f"{summary} Reason: {description}. Safety note: {diff_hint}."

    return f"{summary} Safety note: {diff_hint}."
