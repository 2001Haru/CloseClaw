"""Compatibility layer for external tool ecosystems.

This package provides a stable, internal contract (`ToolSpecV2`) so the
runtime can stay lightweight while supporting multiple external formats.
"""

from .toolspec_v2 import ToolSpecV2
from .native_adapter import NativeAdapter

__all__ = ["ToolSpecV2", "NativeAdapter"]

