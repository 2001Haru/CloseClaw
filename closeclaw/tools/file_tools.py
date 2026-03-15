"""File operation tools."""

import os
import logging
from typing import Any, Optional

from .base import tool, BaseTool
from ..types import Zone, ToolType

logger = logging.getLogger(__name__)


@tool(
    name="read_file",
    description="Read contents of a file",
    zone=Zone.ZONE_A,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "Absolute or relative file path"
        }
    }
)
async def read_file_impl(path: str) -> str:
    """Read file content."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info(f"Read file: {path} ({len(content)} bytes)")
        return content
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        raise


@tool(
    name="write_file",
    description="Write content to a file (overwrites if exists)",
    zone=Zone.ZONE_C,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "File path"
        },
        "content": {
            "type": "string",
            "description": "Content to write"
        }
    }
)
async def write_file_impl(path: str, content: str) -> str:
    """Write content to file."""
    try:
        # Get old content for diff
        old_content = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old_content = f.read()
            except:
                pass
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"Wrote file: {path} ({len(content)} bytes)")
        return f"File written: {path}"
    except Exception as e:
        logger.error(f"Error writing file {path}: {e}")
        raise


@tool(
    name="append_file",
    description="Append content to a file",
    zone=Zone.ZONE_C,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "File path"
        },
        "content": {
            "type": "string",
            "description": "Content to append"
        }
    }
)
async def append_file_impl(path: str, content: str) -> str:
    """Append content to file."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"Appended to file: {path}")
        return f"Content appended to {path}"
    except Exception as e:
        logger.error(f"Error appending to file {path}: {e}")
        raise


@tool(
    name="delete_file",
    description="Delete a file",
    zone=Zone.ZONE_C,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "File path to delete"
        }
    }
)
async def delete_file_impl(path: str) -> str:
    """Delete file."""
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Deleted file: {path}")
            return f"File deleted: {path}"
        else:
            logger.warning(f"File not found: {path}")
            return f"File not found: {path}"
    except Exception as e:
        logger.error(f"Error deleting file {path}: {e}")
        raise


@tool(
    name="list_files",
    description="List files in a directory",
    zone=Zone.ZONE_A,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "Directory path"
        },
        "recursive": {
            "type": "boolean",
            "description": "List recursively"
        }
    }
)
async def list_files_impl(path: str, recursive: bool = False) -> list[str]:
    """List files in directory."""
    try:
        files = []
        if recursive:
            for root, dirs, filenames in os.walk(path):
                for f in filenames:
                    files.append(os.path.join(root, f))
        else:
            for f in os.listdir(path):
                full_path = os.path.join(path, f)
                if os.path.isfile(full_path):
                    files.append(full_path)
        
        logger.info(f"Listed files in {path}: {len(files)} files")
        return files
    except Exception as e:
        logger.error(f"Error listing files in {path}: {e}")
        raise


@tool(
    name="file_exists",
    description="Check if file exists",
    zone=Zone.ZONE_A,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "File path"
        }
    }
)
async def file_exists_impl(path: str) -> bool:
    """Check if file exists."""
    exists = os.path.exists(path) and os.path.isfile(path)
    logger.info(f"File {path} exists: {exists}")
    return exists


@tool(
    name="get_file_size",
    description="Get file size in bytes",
    zone=Zone.ZONE_A,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "File path"
        }
    }
)
async def get_file_size_impl(path: str) -> int:
    """Get file size."""
    try:
        if os.path.exists(path):
            size = os.path.getsize(path)
            logger.info(f"File {path} size: {size} bytes")
            return size
        else:
            raise FileNotFoundError(f"File not found: {path}")
    except Exception as e:
        logger.error(f"Error getting file size {path}: {e}")
        raise
