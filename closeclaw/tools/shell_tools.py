"""Shell command execution tools."""

import subprocess
import logging
from typing import Any, Optional
import platform

from .base import tool
from ..types import Zone, ToolType

logger = logging.getLogger(__name__)


@tool(
    name="shell",
    description="Execute a shell command (Windows CMD or Unix shell)",
    zone=Zone.ZONE_C,
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
    """Execute shell command and return output.
    
    Returns:
        {
            "returncode": int,
            "stdout": str,
            "stderr": str,
            "executed": bool
        }
    """
    try:
        logger.info(f"Executing shell command: {command[:100]}")
        
        # Determine shell based on OS
        shell_type = "cmd.exe" if platform.system() == "Windows" else "/bin/bash"
        use_shell = True
        
        # Execute command
        process = subprocess.run(
            command,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        result = {
            "returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "executed": True,
        }
        
        logger.info(f"Shell command executed: retcode={process.returncode}")
        return result
        
    except subprocess.TimeoutExpired:
        logger.error(f"Shell command timed out after {timeout}s: {command[:100]}")
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds",
            "executed": False,
        }
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
    zone=Zone.ZONE_A,
    tool_type=ToolType.SHELL,
    parameters={}
)
async def pwd_impl() -> str:
    """Get current working directory."""
    import os
    cwd = os.getcwd()
    logger.info(f"Current directory: {cwd}")
    return cwd
