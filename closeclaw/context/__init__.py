"""Context management module for Phase 4 Memory Upgrade.

Provides:
- Token counting and monitoring
- Message summarization and compression
- Context management with sliding windows
"""

from .manager import ContextManager
from .compaction import MessageCompactor

__all__ = ["ContextManager", "MessageCompactor"]

