"""Workspace memory layout utilities for Phase4 patch."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

MEMORY_ROOT_DIRNAME = "CloseClaw Memory"
DEFAULT_STATE_FILE_REL = f"{MEMORY_ROOT_DIRNAME}/state.json"
DEFAULT_MEMORY_DB_REL = f"{MEMORY_ROOT_DIRNAME}/memory.sqlite"
DAILY_MEMORY_SUBDIR_REL = f"{MEMORY_ROOT_DIRNAME}/memory"
DEFAULT_AUDIT_LOG_REL = f"{MEMORY_ROOT_DIRNAME}/audit.log"

PROJECT_CONTEXT_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "SKILLS.md"]


def _default_project_context_content(filename: str) -> str:
    template_path = Path(__file__).with_name(filename)
    if template_path.exists() and template_path.is_file():
        return template_path.read_text(encoding="utf-8")
    return ""


def _default_heartbeat_content() -> str:
    template_path = Path(__file__).parents[1] / "heartbeat" / "HEARTBEAT.md"
    if template_path.exists() and template_path.is_file():
        return template_path.read_text(encoding="utf-8")
    return ""


def memory_root_dir(workspace_root: str) -> str:
    return str(Path(workspace_root) / MEMORY_ROOT_DIRNAME)


def daily_memory_dir(workspace_root: str) -> str:
    return str(Path(workspace_root) / DAILY_MEMORY_SUBDIR_REL)


def ensure_workspace_memory_layout(workspace_root: str) -> None:
    """Create required CloseClaw Memory directory tree and baseline files."""
    root = Path(memory_root_dir(workspace_root))
    daily = Path(daily_memory_dir(workspace_root))
    root.mkdir(parents=True, exist_ok=True)
    daily.mkdir(parents=True, exist_ok=True)

    baseline_files = [
        root / "MEMORY.md",
        root / "state.json",
        root / "HEARTBEAT.md",
        root / "AGENTS.md",
        root / "SOUL.md",
        root / "USER.md",
        root / "TOOLS.md",
        root / "SKILLS.md",
    ]

    for path in baseline_files:
        if path.exists():
            continue
        if path.name == "state.json":
            path.write_text("{}\n", encoding="utf-8")
        elif path.name == "HEARTBEAT.md":
            path.write_text(_default_heartbeat_content(), encoding="utf-8")
        elif path.name in PROJECT_CONTEXT_FILES:
            path.write_text(_default_project_context_content(path.name), encoding="utf-8")
        else:
            path.write_text("", encoding="utf-8")

    today_file = Path(daily_memory_file_path(workspace_root))
    if not today_file.exists():
        today_file.write_text("", encoding="utf-8")


def migrate_legacy_memory_artifacts(workspace_root: str) -> None:
    """Copy legacy scattered files into unified CloseClaw Memory layout when missing."""
    ensure_workspace_memory_layout(workspace_root)
    root = Path(workspace_root)
    mem_root = Path(memory_root_dir(workspace_root))
    daily_dir = Path(daily_memory_dir(workspace_root))

    legacy_state = root / "state.json"
    target_state = mem_root / "state.json"
    if legacy_state.exists() and (not target_state.exists() or target_state.read_text(encoding="utf-8").strip() in {"", "{}"}):
        shutil.copy2(legacy_state, target_state)

    legacy_db = root / "memory" / "memory.sqlite"
    target_db = mem_root / "memory.sqlite"
    if legacy_db.exists() and not target_db.exists():
        shutil.copy2(legacy_db, target_db)

    legacy_daily_dir = root / "memory"
    if legacy_daily_dir.exists() and legacy_daily_dir.is_dir():
        for file in legacy_daily_dir.glob("*.md"):
            target = daily_dir / file.name
            if not target.exists():
                shutil.copy2(file, target)

        # Legacy root-level `memory/` should not be used anymore. If it is empty,
        # remove it to avoid repeatedly surfacing a confusing empty folder.
        try:
            next(legacy_daily_dir.iterdir())
        except StopIteration:
            legacy_daily_dir.rmdir()
        except OSError:
            # Keep directory if deletion is not possible (permissions/race).
            pass


def daily_memory_file_path(workspace_root: str, now: datetime | None = None) -> str:
    ts = now or datetime.now()
    filename = ts.strftime("%Y-%m-%d") + ".md"
    return str(Path(daily_memory_dir(workspace_root)) / filename)
