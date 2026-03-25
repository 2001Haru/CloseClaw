"""Background task management service.

Extracted from AgentCore to isolate TaskManager integration and polling logic.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BackgroundTaskService:
    """Manages background task lifecycle via TaskManager.

    Handles:
    - TaskManager attachment and tool handler registration
    - Polling and normalizing completed task results
    - Creating new background tasks
    """

    def __init__(self) -> None:
        self.task_manager: Optional[Any] = None

    def attach(self, task_manager: Any, tools: dict[str, Any]) -> None:
        """Attach a TaskManager and register all current tool handlers.

        Args:
            task_manager: TaskManager instance for background work
            tools: Dict of tool_name -> Tool with handler attribute
        """
        self.task_manager = task_manager

        for tool in tools.values():
            if getattr(tool, "handler", None):
                self.task_manager.register_tool_handler(tool.name, tool.handler)

        logger.info("TaskManager integrated via BackgroundTaskService")

    async def poll(self) -> list[dict[str, Any]]:
        """Poll for completed background tasks.

        Returns:
            Normalized list of completed task results.
        """
        if not self.task_manager:
            return []

        completed_tasks = await self.task_manager.poll_results()

        normalized: list[dict[str, Any]] = []

        if isinstance(completed_tasks, dict):
            for task_id, task in completed_tasks.items():
                if isinstance(task, dict):
                    normalized.append({
                        "task_id": task.get("task_id", task_id),
                        "status": task.get("status"),
                        "result": task.get("result"),
                        "error": task.get("error"),
                    })
                else:
                    normalized.append({
                        "task_id": getattr(task, "task_id", task_id),
                        "status": getattr(getattr(task, "status", None), "value", getattr(task, "status", "unknown")),
                        "result": getattr(task, "result", None),
                        "error": getattr(task, "error", None),
                    })
            return normalized

        if isinstance(completed_tasks, list):
            for task in completed_tasks:
                if isinstance(task, dict):
                    normalized.append(task)
                else:
                    normalized.append({
                        "task_id": str(task),
                        "status": "unknown",
                        "result": None,
                        "error": "Unexpected task result payload type",
                    })
            return normalized

        return normalized

    async def create(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Create a background task for long-running tool execution.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments

        Returns:
            task_id: Unique task identifier (e.g., "#001")
        """
        if not self.task_manager:
            raise RuntimeError("TaskManager not configured")

        task_id = await self.task_manager.create_task(tool_name, arguments)
        logger.info(f"Created background task: {task_id}")
        return task_id
