"""CLI commands for task management (Phase 2).

Commands:
  closeclaw tasks           - List all tasks (active and completed)
  closeclaw task <id>       - Query single task status
  closeclaw cancel <id>     - Cancel/terminate a task
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Any
from datetime import datetime
from tabulate import tabulate

from ..agents.task_manager import TaskManager
from ..types import TaskStatus

logger = logging.getLogger(__name__)


class CLITaskManager:
    """CLI interface for task management via state.json."""
    
    def __init__(self, state_file: Path = Path("state.json")):
        """Initialize CLI task manager.
        
        Args:
            state_file: Path to state.json file
        """
        self.state_file = state_file
        self.task_manager = TaskManager()
    
    def load_state(self) -> dict[str, Any]:
        """Load state from state.json."""
        if not self.state_file.exists():
            return {}
        
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            return {}
    
    async def initialize_from_state(self) -> None:
        """Restore TaskManager from saved state."""
        state = self.load_state()
        if state:
            await self.task_manager.load_from_state(state)
            logger.info(f"Restored {len(self.task_manager.completed_results)} tasks from state")
    
    def list_tasks(self, verbose: bool = False) -> str:
        """List all tasks (active and completed).
        
        Args:
            verbose: Show detailed information
        
        Returns:
            Formatted table string
        """
        # Combine active and completed tasks
        all_tasks = []
        
        # Active tasks
        for task_id, task_obj in self.task_manager.active_tasks.items():
            all_tasks.append({
                "task_id": task_id,
                "tool": task_obj.tool_name,
                "status": task_obj.status.value,
                "created_at": task_obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "age_seconds": int((datetime.utcnow() - task_obj.created_at).total_seconds()),
            })
        
        # Completed tasks
        for task_id, task_obj in self.task_manager.completed_results.items():
            all_tasks.append({
                "task_id": task_id,
                "tool": task_obj.tool_name,
                "status": task_obj.status.value,
                "created_at": task_obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "age_seconds": int((datetime.utcnow() - task_obj.created_at).total_seconds()),
            })
        
        if not all_tasks:
            return "No tasks found."
        
        # Sort by created_at descending (newest first)
        all_tasks.sort(key=lambda x: x["created_at"], reverse=True)
        
        if verbose:
            # Verbose: show all fields
            columns = ["task_id", "tool", "status", "created_at", "age_seconds"]
        else:
            # Compact: subset of fields
            columns = ["task_id", "tool", "status", "age_seconds"]
        
        filtered_tasks = [{k: v for k, v in task.items() if k in columns} for task in all_tasks]
        
        # Format table
        table = tabulate(
            filtered_tasks,
            headers={k: k.replace("_", " ").upper() for k in columns},
            tablefmt="grid",
        )
        
        return table
    
    def get_task_status(self, task_id: str) -> str:
        """Get detailed status for a single task.
        
        Args:
            task_id: Task ID (e.g., "#001")
        
        Returns:
            Formatted status string
        """
        # Normalize task_id (add # if missing)
        if not task_id.startswith("#"):
            task_id = f"#{task_id}"
        
        task = self.task_manager.get_status(task_id)
        if not task:
            return f"Task {task_id} not found."
        
        # Build formatted output
        lines = [
            f"Task ID: {task.task_id}",
            f"Tool: {task.tool_name}",
            f"Status: {task.status.value}",
            f"Created: {task.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        
        if task.started_at:
            lines.append(f"Started: {task.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if task.completed_at:
            lines.append(f"Completed: {task.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")
            duration = (task.completed_at - task.started_at).total_seconds() if task.started_at else 0
            lines.append(f"Duration: {duration:.2f}s")
        
        if task.error:
            lines.append(f"Error: {task.error}")
        
        if task.result:
            result_str = json.dumps(task.result, indent=2)
            if len(result_str) > 500:
                result_str = result_str[:500] + "..."
            lines.append(f"Result: {result_str}")
        
        lines.append(f"Arguments: {json.dumps(task.tool_arguments)}")
        
        return "\n".join(lines)
    
    async def cancel_task(self, task_id: str) -> str:
        """Cancel/terminate a task.
        
        Args:
            task_id: Task ID to cancel
        
        Returns:
            Status message
        """
        # Normalize task_id
        if not task_id.startswith("#"):
            task_id = f"#{task_id}"
        
        # Check if task exists
        task = self.task_manager.get_status(task_id)
        if not task:
            return f"Task {task_id} not found."
        
        # Check if already completed
        if task.status != TaskStatus.RUNNING:
            return f"Task {task_id} is not running (status: {task.status.value})"
        
        # Try to cancel
        success = await self.task_manager.cancel_task(task_id)
        
        if success:
            return f"✓ Task {task_id} cancelled successfully."
        else:
            return f"✗ Failed to cancel task {task_id}."
    
    def show_summary(self) -> str:
        """Show summary of all tasks.
        
        Returns:
            Summary string
        """
        active_count = len(self.task_manager.active_tasks)
        completed_count = len(self.task_manager.completed_results)
        total_count = active_count + completed_count
        
        # Count by status
        status_counts = {}
        for task in list(self.task_manager.active_tasks.values()) + list(self.task_manager.completed_results.values()):
            status = task.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
        
        lines = [
            f"Task Summary",
            f"─────────────────────",
            f"Total tasks: {total_count}",
            f"Active: {active_count}",
            f"Completed: {completed_count}",
        ]
        
        if status_counts:
            lines.append(f"\nStatus breakdown:")
            for status, count in sorted(status_counts.items()):
                lines.append(f"  {status.upper()}: {count}")
        
        return "\n".join(lines)
