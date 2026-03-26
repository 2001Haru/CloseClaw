"""Tools for proactive document and image parsing."""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

from .base import tool
from ..types import ToolType

logger = logging.getLogger(__name__)

# Maximum image file size to prevent context explosion (20 MB)
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _sync_read_image(path: str) -> str:
    """Synchronous image reading helper, executed via executor."""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return f"Error: Image file not found at {path}"

    file_size = file_path.stat().st_size
    if file_size > _MAX_IMAGE_BYTES:
        return f"Error: Image too large ({file_size / 1024 / 1024:.1f} MB). Max allowed: {_MAX_IMAGE_BYTES / 1024 / 1024:.0f} MB."

    with open(file_path, "rb") as f:
        raw = f.read()

    b64_data = base64.b64encode(raw).decode("utf-8")

    ext = file_path.suffix.lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    if ext not in ("jpeg", "png", "webp", "gif"):
        ext = "png"

    data_url = f"data:image/{ext};base64,{b64_data}"
    return f"___VISION_BASE64___:{data_url}"


def _sync_read_pdf(path: str) -> str:
    """Synchronous PDF reading helper, executed via executor."""
    try:
        import pymupdf4llm
    except ImportError:
        return "Error: pymupdf4llm is not installed. Run 'pip install pymupdf4llm'."

    md_text = pymupdf4llm.to_markdown(path)

    # Truncate extremely long PDFs to avoid context overflow
    max_chars = 30000
    if len(md_text) > max_chars:
        md_text = md_text[:max_chars] + "\n\n... [PDF content truncated]"

    return md_text


@tool(
    name="read_pdf",
    description="Extract text from a PDF file. Use this for reading PDF documents, papers, manuals, or slides. Returns the file's text formatted as Markdown.",
    tool_type=ToolType.FILE,
    need_auth=False,
)
async def read_pdf_impl(path: str) -> str:
    try:
        return await asyncio.to_thread(_sync_read_pdf, path)
    except Exception as e:
        return f"Error reading PDF: {e}"


@tool(
    name="read_image",
    description="Read an image from the local filesystem and embed it natively into your visual context. Use this to visually inspect screenshots, diagrams, photos, or plots.",
    tool_type=ToolType.FILE,
    need_auth=False,
)
async def read_image_impl(path: str) -> str:
    try:
        return await asyncio.to_thread(_sync_read_image, path)
    except Exception as e:
        return f"Error reading image: {e}"
