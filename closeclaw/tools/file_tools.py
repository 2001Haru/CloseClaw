"""File operation tools."""

import os
import logging
import difflib
from typing import Any, Optional
from pathlib import Path

from .base import tool, BaseTool
from ..types import ToolType
from ..memory.workspace_layout import MEMORY_ROOT_DIRNAME

logger = logging.getLogger(__name__)

_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
}


def _resolve_memory_path(path: str) -> Path:
    resolved_path = Path(path).expanduser()
    if not resolved_path.is_absolute():
        resolved_path = (Path.cwd() / resolved_path).resolve()
    else:
        resolved_path = resolved_path.resolve()

    if MEMORY_ROOT_DIRNAME not in resolved_path.parts:
        raise PermissionError(
            f"Memory tools only allow paths under '{MEMORY_ROOT_DIRNAME}'. Got: {resolved_path}"
        )

    return resolved_path


@tool(
    name="read_file",
    description="Read contents of a file, optionally by line range",
    need_auth=False,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "Absolute or relative file path"
        },
        "start_line": {
            "type": "integer",
            "description": "Optional 1-based start line to read"
        },
        "end_line": {
            "type": "integer",
            "description": "Optional 1-based end line to read (inclusive)"
        }
    }
)
async def read_file_impl(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Read file content with optional 1-based line range."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if start_line is None and end_line is None:
            logger.info(f"Read file: {path} ({len(content)} bytes)")
            return content

        lines = content.splitlines()
        total_lines = len(lines)

        effective_start = 1 if start_line is None else start_line
        effective_end = total_lines if end_line is None else end_line

        if effective_start < 1:
            raise ValueError("start_line must be >= 1")
        if effective_end < effective_start:
            raise ValueError("end_line must be >= start_line")
        if total_lines == 0:
            return ""
        if effective_start > total_lines:
            raise ValueError(f"start_line {effective_start} is out of range. File has {total_lines} lines")

        effective_end = min(effective_end, total_lines)
        selected = lines[effective_start - 1: effective_end]
        numbered = [f"{effective_start + idx}| {line}" for idx, line in enumerate(selected)]
        result = "\n".join(numbered)
        logger.info(
            "Read file lines: %s (%s-%s/%s)",
            path,
            effective_start,
            effective_end,
            total_lines,
        )
        return result
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        raise


@tool(
    name="write_memory_file",
    description="Save memory/notes to a file (for automatic memory flush). Auto-approved system tool.",
    need_auth=False,
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
        resolved_path = _resolve_memory_path(path)

        # Ensure parent directory exists
        os.makedirs(str(resolved_path.parent), exist_ok=True)
        
        with open(resolved_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.warning(f"[MEMORY_FLUSH] Wrote memory file: {resolved_path} ({len(content)} bytes)")
        return f"Memory saved: {resolved_path}"
    except Exception as e:
        logger.error(f"[MEMORY_FLUSH] Error writing memory file {path}: {e}")
        raise


@tool(
    name="edit_memory_file",
    description="Edit a memory file by replacing old_text with new_text under CloseClaw Memory",
    need_auth=False,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "Memory file path under CloseClaw Memory"
        },
        "old_text": {
            "type": "string",
            "description": "Text block to replace"
        },
        "new_text": {
            "type": "string",
            "description": "Replacement text"
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace all matched occurrences. Defaults to false"
        },
        "dry_run": {
            "type": "boolean",
            "description": "Return preview only without writing changes. Defaults to false"
        },
    }
)
async def edit_memory_file_impl(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    dry_run: bool = False,
) -> str:
    """Edit memory file content with path restriction to CloseClaw Memory."""
    try:
        resolved = _resolve_memory_path(path)
        return _edit_text_file_impl(
            path=str(resolved),
            old_text=old_text,
            new_text=new_text,
            replace_all=replace_all,
            dry_run=dry_run,
        )
    except Exception as e:
        logger.error(f"[MEMORY_FLUSH] Error editing memory file {path}: {e}")
        raise


@tool(
    name="write_file",
    description="Write content to a file (overwrites if exists)",
    need_auth=True,
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
    name="edit_file",
    description="Edit a file by replacing old_text with new_text",
    need_auth=True,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "File path"
        },
        "old_text": {
            "type": "string",
            "description": "Text block to replace"
        },
        "new_text": {
            "type": "string",
            "description": "Replacement text"
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace all matched occurrences. Defaults to false"
        },
        "dry_run": {
            "type": "boolean",
            "description": "Return preview only without writing changes. Defaults to false"
        }
    }
)
async def edit_file_impl(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    dry_run: bool = False,
) -> str:
    """Replace text in file with fuzzy fallback diagnostics."""
    return _edit_text_file_impl(
        path=path,
        old_text=old_text,
        new_text=new_text,
        replace_all=replace_all,
        dry_run=dry_run,
    )


def _edit_text_file_impl(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    dry_run: bool = False,
) -> str:
    """Core implementation for edit-style tools."""
    try:
        if not os.path.exists(path):
            return f"Error: File not found: {path}"

        with open(path, "rb") as f:
            raw = f.read()

        uses_crlf = b"\r\n" in raw
        content = raw.decode("utf-8").replace("\r\n", "\n")
        normalized_old = old_text.replace("\r\n", "\n")
        normalized_new = new_text.replace("\r\n", "\n")

        matched_fragment, count = _find_match(content, normalized_old)
        if matched_fragment is None:
            return _build_not_found_message(path, normalized_old, content)

        if count > 1 and not replace_all:
            return (
                f"Warning: old_text appears {count} times. "
                "Provide more context to make it unique, or set replace_all=true."
            )

        if replace_all:
            updated_content = content.replace(matched_fragment, normalized_new)
        else:
            updated_content = content.replace(matched_fragment, normalized_new, 1)

        if uses_crlf:
            updated_content = updated_content.replace("\n", "\r\n")

        if dry_run:
            return (
                f"Dry run: edit preview for {path} "
                f"(replace_all={replace_all}, changed={updated_content != raw.decode('utf-8')})"
            )

        with open(path, "wb") as f:
            f.write(updated_content.encode("utf-8"))

        logger.info("Edited file: %s (replace_all=%s)", path, replace_all)
        return f"Successfully edited {path}"
    except Exception as e:
        logger.error(f"Error editing file {path}: {e}")
        raise


def _find_match(content: str, old_text: str) -> tuple[Optional[str], int]:
    """Find text block match by exact and stripped-line fallback."""
    if old_text in content:
        return old_text, content.count(old_text)

    old_lines = old_text.splitlines()
    if not old_lines:
        return None, 0

    stripped_old = [line.strip() for line in old_lines]
    content_lines = content.splitlines()
    candidates: list[str] = []

    for idx in range(len(content_lines) - len(stripped_old) + 1):
        window = content_lines[idx: idx + len(stripped_old)]
        if [line.strip() for line in window] == stripped_old:
            candidates.append("\n".join(window))

    if candidates:
        return candidates[0], len(candidates)
    return None, 0


def _build_not_found_message(path: str, old_text: str, content: str) -> str:
    """Return helpful diff hint when old_text cannot be matched."""
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window_size = len(old_lines)
    if window_size == 0:
        return f"Error: old_text not found in {path}."

    best_ratio = 0.0
    best_start = 0
    max_start = max(1, len(lines) - window_size + 1)
    for i in range(max_start):
        ratio = difflib.SequenceMatcher(None, old_lines, lines[i: i + window_size]).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    if best_ratio > 0.5:
        diff = "\n".join(
            difflib.unified_diff(
                old_lines,
                lines[best_start: best_start + window_size],
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            )
        )
        return (
            f"Error: old_text not found in {path}.\n"
            f"Best match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        )

    return f"Error: old_text not found in {path}. No similar text found."


@tool(
    name="delete_file",
    description="Delete a file",
    need_auth=True,
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
    name="delete_lines",
    description="Delete selected lines from a file (inclusive line range)",
    need_auth=True,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "File path"
        },
        "start_line": {
            "type": "integer",
            "description": "1-based start line to delete"
        },
        "end_line": {
            "type": "integer",
            "description": "1-based end line to delete (inclusive). Defaults to start_line when omitted"
        }
    }
)
async def delete_lines_impl(path: str, start_line: int, end_line: Optional[int] = None) -> str:
    """Delete selected lines from a file by inclusive line numbers."""
    try:
        if start_line < 1:
            raise ValueError("start_line must be >= 1")

        effective_end = start_line if end_line is None else end_line
        if effective_end < start_line:
            raise ValueError("end_line must be >= start_line")

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total_lines = len(lines)
        if start_line > total_lines:
            raise ValueError(f"start_line {start_line} is out of range. File has {total_lines} lines")

        effective_end = min(effective_end, total_lines)
        delete_count = effective_end - start_line + 1

        kept = lines[: start_line - 1] + lines[effective_end:]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)

        logger.info(
            "Deleted lines %s-%s from %s (%s lines removed)",
            start_line,
            effective_end,
            path,
            delete_count,
        )
        return (
            f"Deleted lines {start_line}-{effective_end} from {path} "
            f"({delete_count} line(s) removed)"
        )
    except Exception as e:
        logger.error(f"Error deleting lines from {path}: {e}")
        raise


@tool(
    name="list_files",
    description="List directory entries with optional recursion and cap",
    need_auth=False,
    tool_type=ToolType.FILE,
    parameters={
        "path": {
            "type": "string",
            "description": "Directory path"
        },
        "recursive": {
            "type": "boolean",
            "description": "List recursively"
        },
        "max_entries": {
            "type": "integer",
            "description": "Maximum entries to return (default 500)"
        }
    }
)
async def list_files_impl(path: str, recursive: bool = False, max_entries: Optional[int] = None) -> list[str]:
    """List directory entries.

    - recursive=False: return only immediate children (both folders and files)
    - recursive=True: return files up to one level deep (legacy behavior)
    """
    try:
        files: list[str] = []
        # Hard limit to prevent token overflow on massive directories
        MAX_FILES = max_entries or 500
        limit_reached = False

        root_path = Path(path)

        if recursive:
            for root, dirs, filenames in os.walk(path):
                if limit_reached:
                    break

                dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]

                for f in filenames:
                    if len(files) >= MAX_FILES:
                        limit_reached = True
                        break

                    absolute = Path(root) / f
                    rel = absolute.relative_to(root_path)
                    files.append(str(rel).replace("\\", "/"))
        else:
            for f in os.listdir(path):
                if f in _IGNORE_DIRS:
                    continue
                if len(files) >= MAX_FILES:
                    limit_reached = True
                    break
                full_path = os.path.join(path, f)
                if os.path.isdir(full_path):
                    files.append(f"{f}/")
                elif os.path.isfile(full_path):
                    files.append(f)
        
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
    need_auth=False,
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
    need_auth=False,
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

