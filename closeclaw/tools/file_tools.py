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
    name="write_memory_file",
    description="Save memory/notes to a file (for automatic memory flush). Auto-approved system tool.",
    zone=Zone.ZONE_A,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "Absolute file path for memory file"
        },
        "content": {
            "type": "string",
            "description": "Memory content in Markdown format"
        }
    }
)
async def write_memory_file_impl(path: str, content: str) -> str:
    """Write memory file (system auto-flush)."""
    try:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.warning(f"[MEMORY_FLUSH] 💾 Wrote memory file: {path} ({len(content)} bytes)")
        return f"Memory saved: {path}"
    except Exception as e:
        logger.error(f"[MEMORY_FLUSH] ❌ Error writing memory file {path}: {e}")
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
        # Hard limit to prevent token overflow on massive directories
        MAX_FILES = 500
        limit_reached = False
        
        # Calculate target depth for 'recursive'
        # To avoid token explosion, we limit "recursive" to just 1 level deep (depth=1)
        base_depth = path.rstrip(os.path.sep).count(os.path.sep)
        
        if recursive:
            for root, dirs, filenames in os.walk(path):
                if limit_reached:
                    break
                    
                current_depth = root.rstrip(os.path.sep).count(os.path.sep)
                if current_depth > base_depth + 1:
                    # Do not descend further than 1 level deep
                    dirs.clear()
                    continue
                    
                for f in filenames:
                    if len(files) >= MAX_FILES:
                        limit_reached = True
                        break
                    files.append(os.path.join(root, f))
        else:
            for f in os.listdir(path):
                if len(files) >= MAX_FILES:
                    limit_reached = True
                    break
                full_path = os.path.join(path, f)
                if os.path.isfile(full_path):
                    files.append(full_path)
        
        if limit_reached:
            logger.warning(f"File limit reached when listing {path}. Only returning first {MAX_FILES}.")
            files.append(f"... (Truncated. Only first {MAX_FILES} files shown out of many.)")
            
        logger.info(f"Listed files in {path}: {len(files)} items")
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
