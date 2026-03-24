"""Consensus sentinel reviewer for autonomous authorization decisions."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class GuardianDecision:
    approved: bool
    reason_code: str
    comment: str


class ConsensusGuardian:
    """LLM-backed sentinel that reviews sensitive tool requests."""

    DEFAULT_PROMPT = (
        "You are a upright and excellent supervisor. You check the reason and diff preview provided by the worker agent when they are calling a function."
        "You decide whether to approve or reject the action. Output JSON with keys: decision (approve|reject), reason_code, comment."
        "You approve the agent's action most of time for efficiency reasons, but reject it if there is a clear risk of harm or violation of ethical principles."
        "Reject dangerous commands, such as attempts to delete all files; Approve routine commands, such as editing a document and running a test."
        "Always give a clear reason_code and comment to help the worker agent understand your decision."
    )

    def __init__(self, llm_provider: Any, prompt: str | None = None, timeout_seconds: float = 20.0):
        self._llm_provider = llm_provider
        self._prompt = (prompt or self.DEFAULT_PROMPT).strip()
        self._timeout_seconds = float(timeout_seconds)

    async def review(self, payload: dict[str, Any]) -> GuardianDecision:
        messages = [
            {"role": "system", "content": self._prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]

        try:
            text, _ = await asyncio.wait_for(
                self._llm_provider.generate(messages=messages, tools=[]),
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            return GuardianDecision(
                approved=False,
                reason_code="GUARDIAN_ERROR",
                comment=f"Sentinel unavailable: {exc}",
            )

        decision = self._parse_decision(text or "")
        if decision is None:
            return GuardianDecision(
                approved=False,
                reason_code="GUARDIAN_PARSE_ERROR",
                comment="Sentinel response was not parseable as policy decision",
            )
        return decision

    def _parse_decision(self, text: str) -> GuardianDecision | None:
        text = text.strip()
        parsed: dict[str, Any] | None = None

        try:
            parsed = json.loads(text)
        except Exception:
            left = text.find("{")
            right = text.rfind("}")
            if left != -1 and right != -1 and right > left:
                try:
                    parsed = json.loads(text[left : right + 1])
                except Exception:
                    parsed = None

        if not isinstance(parsed, dict):
            lowered = text.lower()
            if "approve" in lowered and "reject" not in lowered:
                return GuardianDecision(True, "GUARDIAN_APPROVED_TEXT", text[:200])
            if "reject" in lowered:
                return GuardianDecision(False, "GUARDIAN_REJECTED_TEXT", text[:200])
            return None

        raw_decision = str(parsed.get("decision", "reject")).strip().lower()
        approved = raw_decision == "approve"
        return GuardianDecision(
            approved=approved,
            reason_code=str(parsed.get("reason_code", "GUARDIAN_DECISION")),
            comment=str(parsed.get("comment", "")),
        )
