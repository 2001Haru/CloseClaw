"""Security mode definitions and helpers."""

from __future__ import annotations

from enum import Enum


class SecurityMode(str, Enum):
    AUTONOMOUS = "autonomous"
    SUPERVISED = "supervised"
    CONSENSUS = "consensus"


def normalize_security_mode(value: str | SecurityMode | None) -> SecurityMode:
    if isinstance(value, SecurityMode):
        return value

    raw = (value or SecurityMode.SUPERVISED.value).strip().lower()
    if raw == SecurityMode.AUTONOMOUS.value:
        return SecurityMode.AUTONOMOUS
    if raw == SecurityMode.CONSENSUS.value:
        return SecurityMode.CONSENSUS
    return SecurityMode.SUPERVISED
