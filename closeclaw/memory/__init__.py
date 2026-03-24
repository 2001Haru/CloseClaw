"""Memory management module for Phase 4.

Provides memory flush session coordination for automatic saving of important discussions
before context compression.
"""

from .memory_flush import MemoryFlushSession, MemoryFlushCoordinator
from .memory_manager import MemoryManager
from .workspace_layout import (
	MEMORY_ROOT_DIRNAME,
	DEFAULT_STATE_FILE_REL,
	DEFAULT_MEMORY_DB_REL,
	DEFAULT_AUDIT_LOG_REL,
	DAILY_MEMORY_SUBDIR_REL,
	PROJECT_CONTEXT_FILES,
	memory_root_dir,
	daily_memory_dir,
	ensure_workspace_memory_layout,
	migrate_legacy_memory_artifacts,
	daily_memory_file_path,
)

__all__ = [
	"MemoryFlushSession",
	"MemoryFlushCoordinator",
	"MemoryManager",
	"MEMORY_ROOT_DIRNAME",
	"DEFAULT_STATE_FILE_REL",
	"DEFAULT_MEMORY_DB_REL",
	"DEFAULT_AUDIT_LOG_REL",
	"DAILY_MEMORY_SUBDIR_REL",
	"PROJECT_CONTEXT_FILES",
	"memory_root_dir",
	"daily_memory_dir",
	"ensure_workspace_memory_layout",
	"migrate_legacy_memory_artifacts",
	"daily_memory_file_path",
]


