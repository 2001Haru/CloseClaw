"""Shell command execution tools.

Uses asyncio.create_subprocess_shell for true non-blocking execution,
enabling safe TaskManager integration.
"""

import asyncio
import logging
from typing import Any, Optional
import platform

from .base import tool
from ..types import ToolType

logger = logging.getLogger(__name__)


@tool(
    name="shell",
    description="Execute a shell command (Windows CMD or Unix shell)",
    need_auth=True,
    tool_type=ToolType.SHELL,
    parameters={
        "command": {
            "type": "string",
            "description": "Shell command to execute"
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds (default: 30)"
        }
    }
)
async def shell_impl(command: str, timeout: int = 30) -> dict[str, Any]:
    """Execute shell command asynchronously and return output.
    
    Uses asyncio.create_subprocess_shell for non-blocking execution.
    This ensures the event loop remains responsive while the command runs,
    which is critical for TaskManager background task support.
    
    Returns:
        {
            "returncode": int,
            "stdout": str,
            "stderr": str,
            "executed": bool
        }
    """
    try:
        logger.info(f"Executing shell command (async): {command[:100]}")
        
        # Create async subprocess
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Wait for completion with timeout
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Kill the process on timeout
            process.kill()
            await process.wait()
            logger.error(f"Shell command timed out after {timeout}s: {command[:100]}")
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "executed": False,
            }
        
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        
        result = {
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "executed": True,
        }
        
        logger.info(f"Shell command executed: retcode={process.returncode}")
        return result
        
    except Exception as e:
        logger.error(f"Shell execution error: {e}")
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "executed": False,
        }


@tool(
    name="pwd",
    description="Print current working directory",
    need_auth=False,
    tool_type=ToolType.SHELL,
    parameters={}
)
async def pwd_impl() -> str:
    """Get current working directory."""
    import os
    cwd = os.getcwd()
    logger.info(f"Current directory: {cwd}")
    return cwd

