"""System prompt builder service.

Extracted from AgentCore to centralize multi-layer system prompt construction.
"""

import logging
import os
import re
from typing import Any, Optional
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from ..types import AgentConfig, Tool
from ..memory.workspace_layout import PROJECT_CONTEXT_FILES, memory_root_dir

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Builds multi-layer system prompts with project context and work information.

    Centralizes all prompt construction logic previously spread across AgentCore.
    """

    def __init__(
        self,
        *,
        config: AgentConfig,
        workspace_root: str,
        repo_root: str,
        tools: dict[str, Tool],
        skills_loader: Any,
        context_service: Any,
    ):
        self.config = config
        self.workspace_root = workspace_root
        self.repo_root = repo_root
        self.tools = tools
        self.skills_loader = skills_loader
        self.context_service = context_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, suffix: str = "") -> str:
        """Build the full system prompt with all layers."""
        context_block = self.project_context_block()
        work_info_block = self.work_information_block()
        native_tools_block = self.native_tools_block()
        always_skills_block = self.always_skills_block()
        skills_summary_block = self.skills_summary_block()

        base_prompt = self.config.system_prompt or ""
        extras: list[str] = [
            native_tools_block,
            context_block,
            work_info_block,
            always_skills_block,
            skills_summary_block,
        ]
        combined_base_prompt = "\n\n".join([p for p in [base_prompt, *extras] if p])

        return self.context_service.build_system_prompt(
            base_prompt=combined_base_prompt,
            has_retrieve_memory_tool="retrieve_memory" in self.tools,
            suffix=suffix,
        )

    # ------------------------------------------------------------------
    # Block builders
    # ------------------------------------------------------------------

    def project_context_block(self) -> str:
        """Build [PROJECT CONTEXT] block from workspace memory files."""
        root = memory_root_dir(self.workspace_root)
        sections: list[str] = []
        for name in PROJECT_CONTEXT_FILES:
            path = os.path.join(root, name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except Exception:
                continue
            if not content:
                continue
            sections.append(f"[{name}]\n{content}")

        if not sections:
            return ""
        return "[PROJECT CONTEXT]\n" + "\n\n".join(sections)

    def work_information_block(self) -> str:
        """Build [WORK INFORMATION] block with current time and workspace paths."""
        now_utc = datetime.now(timezone.utc)
        work_tz, tz_label = self.resolve_work_timezone()
        now_local = now_utc.astimezone(work_tz)
        return (
            "[WORK INFORMATION]\n"
            f"current_time_utc: {now_utc.isoformat()}\n"
            f"configured_utc_timezone: {tz_label}\n"
            f"current_time_configured: {now_local.isoformat()}\n"
            f"workspace_root: {self.workspace_root}\n"
            f"closeclaw_repository_root: {self.repo_root}"
        )

    def native_tools_block(self) -> str:
        """Build [NATIVE TOOLS] block.
        
        NOTE: Returning empty string to avoid embedding plain-text tool descriptions
        in the system prompt, which causes models like Gemini to 'speak' tool calls
        as text rather than using the structured API. The schema is passed via
        the native `tools` array automatically.
        """
        return ""

    def always_skills_block(self) -> str:
        """Build [ALWAYS SKILLS] block from skills loader."""
        always_skills = self.skills_loader.get_always_skills()
        if not always_skills:
            return ""

        content = self.skills_loader.load_skills_for_context(always_skills)
        if not content:
            return ""

        return "[ALWAYS SKILLS]\n" + content

    def skills_summary_block(self) -> str:
        """Build [SKILLS INDEX] block summarising available skills."""
        summary = self.skills_loader.build_skills_summary().strip()
        if not summary:
            return ""

        return (
            "[SKILLS INDEX]\n"
            "Use read_file to load a full SKILL.md when the task requires specialized workflow.\n"
            f"{summary}"
        )

    # ------------------------------------------------------------------
    # Timezone helper (moved from AgentCore)
    # ------------------------------------------------------------------

    def resolve_work_timezone(self) -> tuple[Any, str]:
        """Resolve configured work timezone from metadata.

        Supports: IANA names (e.g. Asia/Shanghai), UTC, UTC+08:00, UTC-5.
        """
        configured = str(
            getattr(self.config, "work_time_timezone", None)
            or self.config.metadata.get("work_time_timezone", "UTC")
            or "UTC"
        ).strip()
        if not configured:
            return timezone.utc, "UTC"

        if configured.upper() == "UTC":
            return timezone.utc, "UTC"

        m = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", configured, flags=re.IGNORECASE)
        if m:
            sign = 1 if m.group(1) == "+" else -1
            hours = int(m.group(2))
            minutes = int(m.group(3) or "0")
            if hours <= 23 and minutes <= 59:
                offset = timedelta(hours=hours, minutes=minutes) * sign
                return timezone(offset), configured.upper()

        if ZoneInfo is not None:
            try:
                return ZoneInfo(configured), configured
            except Exception:
                logger.warning("Invalid work_time_timezone '%s', fallback to UTC", configured)

        return timezone.utc, "UTC"
