"""Background task manager for long-running async operations.

TaskManager handles:
1. Creating async background tasks (asyncio.create_task)
2. Polling for task completion
3. Storing and retrieving task results
4. Persisting task state to state.json

Design:
- Synchronous API (easy to integrate with sync main loop)
- Wraps asyncio.create_task() for background execution
- Non-blocking polling mechanism
- Task status tracking: pending → running → completed/failed/cancelled
"""

import asyncio
import json
import logging
from typing import Any, Optional, Callable, Dict
from datetime import datetime
from pathlib import Path

from ..types import BackgroundTask, TaskStatus

logger = logging.getLogger(__name__)


class TaskManager:
    """Manages background task lifecycle for long-running tool executions."""
    
    def __init__(self, state_file: str = "state.json"):
        """Initialize TaskManager.
        
        Args:
            state_file: Path to state.json for persistence
        """
        self.state_file = state_file
        self.task_counter = 0
        
        # Active tasks: task_id -> asyncio.Task
        self.active_tasks: Dict[str, asyncio.Task] = {}
        
        # Completed results: task_id -> (status, result, error)
        self.completed_results: Dict[str, BackgroundTask] = {}
        
        # Tool handlers registry: tool_name -> callable
        self.tool_handlers: Dict[str, Callable] = {}
    
    def register_tool_handler(self, tool_name: str, handler: Callable) -> None:
        """Register a tool handler for background execution.
        
        Args:
            tool_name: Name of the tool
            handler: Async callable that executes the tool
        """
        self.tool_handlers[tool_name] = handler
        logger.info(f"Registered tool handler: {tool_name}")
    
    async def create_task(self, 
                         tool_name: str, 
                         arguments: Dict[str, Any],
                         expires_after: int = 3600) -> str:
        """Create a new background task.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool
            expires_after: Task expiration time in seconds (default: 1 hour)
        
        Returns:
            task_id: Unique task identifier (format: "#001", "#002", etc.)
        
        Flow:
            1. Generate task_id
            2. Create BackgroundTask object
            3. Create asyncio.Task via create_task()
            4. Store in active_tasks
            5. Return task_id immediately
        """
        # Generate task_id
        self.task_counter += 1
        task_id = f"#{self.task_counter:03d}"
        
        # Create BackgroundTask metadata
        task_obj = BackgroundTask(
            task_id=task_id,
            tool_name=tool_name,
            tool_arguments=arguments,
            status=TaskStatus.PENDING,
            created_at=datetime.utcnow(),
            expires_after=expires_after,
        )
        
        # Get tool handler
        handler = self.tool_handlers.get(tool_name)
        if not handler:
            task_obj.status = TaskStatus.FAILED
            task_obj.error = f"Tool '{tool_name}' not registered"
            self.completed_results[task_id] = task_obj
            logger.error(f"Tool handler not found: {tool_name}")
            return task_id
        
        # Create async wrapper
        async def run_task():
            """Execute tool in background and track status."""
            try:
                task_obj.status = TaskStatus.RUNNING
                task_obj.started_at = datetime.utcnow()
                
                logger.info(f"[{task_id}] Starting: {tool_name}")
                
                # Execute tool
                result = await handler(**arguments)
                
                # Store result
                task_obj.status = TaskStatus.COMPLETED
                task_obj.result = result
                task_obj.completed_at = datetime.utcnow()
                
                logger.info(f"[{task_id}] Completed: {tool_name}")
                
            except asyncio.CancelledError:
                task_obj.status = TaskStatus.CANCELLED
                task_obj.error = "Task cancelled"
                task_obj.completed_at = datetime.utcnow()
                logger.warning(f"[{task_id}] Cancelled: {tool_name}")
                
            except Exception as e:
                task_obj.status = TaskStatus.FAILED
                task_obj.error = str(e)
                task_obj.completed_at = datetime.utcnow()
                logger.error(f"[{task_id}] Error: {tool_name}: {e}")
            
            finally:
                # Move to completed results
                self.completed_results[task_id] = task_obj
        
        # Create and store asyncio task
        async_task = asyncio.create_task(run_task())
        self.active_tasks[task_id] = async_task
        
        logger.info(f"Created background task: {task_id} (tool={tool_name})")
        return task_id
    
    async def poll_results(self) -> Dict[str, BackgroundTask]:
        """Poll for completed tasks and return their results.
        
        This method should be called from the main loop on each iteration.
        
        Returns:
            Dictionary of task_id -> BackgroundTask for newly completed tasks
        
        Flow:
            1. Check each active task
            2. If done, move to completed_results
            3. Return completed tasks
        
        Note: Does NOT block - returns immediately even if no tasks completed
        """
        newly_completed = {}
        
        # Check which active tasks are done
        for task_id in list(self.active_tasks.keys()):
            task = self.active_tasks[task_id]
            
            if task.done():
                # Task finished (normal, error, or cancelled)
                # The result is already in self.completed_results via run_task()
                newly_completed[task_id] = self.completed_results[task_id]
                
                # Remove from active tasks
                del self.active_tasks[task_id]
                
                logger.debug(f"Discovered completed task: {task_id}")
        
        return newly_completed
    
    def get_status(self, task_id: str) -> Optional[BackgroundTask]:
        """Get status of a specific task.
        
        Args:
            task_id: Task identifier
        
        Returns:
            BackgroundTask object with current status, or None if not found
        
        Task location:
            - If still running: retrieved from active_tasks and reconstructed
            - If completed: retrieved from completed_results
        """
        # Check completed results first
        if task_id in self.completed_results:
            return self.completed_results[task_id]
        
        # Check active tasks
        if task_id in self.active_tasks:
            # Reconstruct BackgroundTask from asyncio.Task
            task = self.active_tasks[task_id]
            # We don't have full metadata for active tasks, return None for now
            # (user should use poll_results() to track active tasks)
            return None
        
        return None
    
    def list_active_tasks(self) -> Dict[str, BackgroundTask]:
        """List all active and completed tasks.
        
        Returns:
            Dictionary of task_id -> BackgroundTask for all tracked tasks
        """
        all_tasks = {}
        all_tasks.update(self.completed_results)
        return all_tasks
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task.
        
        Args:
            task_id: Task identifier
        
        Returns:
            True if task was cancelled, False if not found or already done
        """
        if task_id not in self.active_tasks:
            return False
        
        task = self.active_tasks[task_id]
        cancelled = task.cancel()
        
        if cancelled:
            logger.info(f"Cancelled task: {task_id}")
        
        return cancelled
    
    async def wait_for_task(self, 
                           task_id: str, 
                           timeout: Optional[float] = None) -> Optional[BackgroundTask]:
        """Wait for a specific task to complete (blocking).
        
        Args:
            task_id: Task identifier
            timeout: Timeout in seconds (None = wait forever)
        
        Returns:
            BackgroundTask with final status, or None if timeout
        """
        if task_id not in self.active_tasks:
            # Already completed
            return self.completed_results.get(task_id)
        
        try:
            task = self.active_tasks[task_id]
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Task {task_id} timed out after {timeout}s")
            return None
        
        # Return completed result
        return self.completed_results.get(task_id)
    
    async def load_from_state(self, state_data: Dict[str, Any]) -> None:
        """Load task state from state.json.
        
        Args:
            state_data: Parsed state.json content
        
        Flow:
            1. Clear current state
            2. Restore completed_results from persistence
            3. Mark any interrupted active_tasks as FAILED
        
        Note: active_tasks cannot be serialized (contains asyncio.Task objects),
              so on restart they're lost. We only restore completed tasks.
        """
        from datetime import datetime
        from closeclaw.types import TaskStatus
        
        # Clear existing state
        self.completed_results.clear()
        self.active_tasks.clear()
        
        if not state_data:
            return
        
        # Restore completed results
        completed_results_data = state_data.get("completed_results", {})
        for task_id, task_data in completed_results_data.items():
            try:
                # Parse datetimes from ISO format
                created_at = datetime.fromisoformat(task_data["created_at"])
                started_at = None
                if task_data.get("started_at"):
                    started_at = datetime.fromisoformat(task_data["started_at"])
                completed_at = None
                if task_data.get("completed_at"):
                    completed_at = datetime.fromisoformat(task_data["completed_at"])
                
                # Reconstruct BackgroundTask object
                task_obj = BackgroundTask(
                    task_id=task_data["task_id"],
                    tool_name=task_data["tool_name"],
                    tool_arguments=task_data["tool_arguments"],
                    expires_after=task_data["expires_after"],
                )
                
                # Restore task state
                task_obj.status = TaskStatus(task_data["status"])
                task_obj.created_at = created_at
                task_obj.started_at = started_at
                task_obj.completed_at = completed_at
                task_obj.result = task_data.get("result")
                task_obj.error = task_data.get("error")
                
                # Store in completed_results dict
                self.completed_results[task_id] = task_obj
                logger.info(f"Restored task from state: {task_id} ({task_obj.status})")
                
            except Exception as e:
                logger.error(f"Failed to restore task {task_id}: {e}")
    
    async def save_to_state(self) -> Dict[str, Any]:
        """Prepare task state for persistence.
        
        Returns:
            Dictionary suitable for json.dump() to state.json
        
        Structure:
            {
                "active_tasks": {
                    "#001": {task_data...},
                    "#002": {task_data...},
                },
                "completed_results": {
                    "#000": {task_data...},
                }
            }
        
        Note: active_tasks with still-running asyncio.Task cannot be serialized,
              so we only save metadata. On restore, they'll be marked as failed.
        """
        completed_data = {}
        
        # Serialize completed results only
        for task_id, task_obj in self.completed_results.items():
            completed_data[task_id] = {
                "task_id": task_obj.task_id,
                "tool_name": task_obj.tool_name,
                "tool_arguments": task_obj.tool_arguments,
                "status": task_obj.status.value,
                "created_at": task_obj.created_at.isoformat(),
                "started_at": task_obj.started_at.isoformat() if task_obj.started_at else None,
                "completed_at": task_obj.completed_at.isoformat() if task_obj.completed_at else None,
                "result": task_obj.result,
                "error": task_obj.error,
                "expires_after": task_obj.expires_after,
            }
        
        return {
            "active_tasks": {},
            "completed_results": completed_data,
        }
    
    async def cleanup_expired_tasks(self, max_age_seconds: int = 86400) -> int:
        """Remove tasks older than max_age_seconds.
        
        Args:
            max_age_seconds: Age threshold in seconds (default: 24 hours)
        
        Returns:
            Number of tasks removed
        
        Use case: Prevent state.json from growing unbounded
        """
        now = datetime.utcnow()
        expired_count = 0
        
        to_remove = []
        for task_id, task_obj in self.completed_results.items():
            age_seconds = (now - task_obj.created_at).total_seconds()
            if age_seconds > max_age_seconds:
                to_remove.append(task_id)
        
        for task_id in to_remove:
            del self.completed_results[task_id]
            expired_count += 1
            logger.debug(f"Cleaned up expired task: {task_id}")
        
        return expired_count
