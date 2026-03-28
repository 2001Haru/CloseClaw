"""OS-level sandbox executor facade."""

from __future__ import annotations

import asyncio
import logging
import platform
from typing import Any, Optional

from .windows_restricted import run_restricted_shell_windows

logger = logging.getLogger(__name__)


class OSSandboxExecutor:
    """Runs selected tools through OS-level restricted execution."""

    async def run_shell(
        self,
        *,
        command: str,
        timeout: int,
        cwd: Optional[str],
        env: dict[str, str],
        fail_closed: bool,
    ) -> Optional[dict[str, Any]]:
        system = platform.system().lower()
        if system != "windows":
            return None

        result = await asyncio.to_thread(
            run_restricted_shell_windows,
            command=command,
            timeout=timeout,
            cwd=cwd,
            env=env,
            fail_closed=fail_closed,
        )
        if result is None:
            logger.warning("OS sandbox did not handle shell call; falling back to normal execution")
        return result


_EXECUTOR = OSSandboxExecutor()


def get_os_sandbox_executor() -> OSSandboxExecutor:
    """Return process-global sandbox executor instance."""
    return _EXECUTOR

