"""Runtime subagent manager registry and manager exports."""

from .manager import SubagentManager

_runtime_subagent_manager: SubagentManager | None = None


def set_runtime_subagent_manager(manager: SubagentManager | None) -> None:
    global _runtime_subagent_manager
    _runtime_subagent_manager = manager


def get_runtime_subagent_manager() -> SubagentManager | None:
    return _runtime_subagent_manager


__all__ = [
    "SubagentManager",
    "set_runtime_subagent_manager",
    "get_runtime_subagent_manager",
]
