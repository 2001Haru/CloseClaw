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
from datetime import datetime, timezone
from tabulate import tabulate
import yaml

from ..agents.task_manager import TaskManager
from ..config import ConfigLoader
from ..heartbeat import HeartbeatService
from ..cron import CronService, CronSchedule
from ..mcp import MCPClientPool
from ..mcp.transport import MCPHttpClient, MCPStdioClient
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
        now_utc = datetime.now(timezone.utc)
        all_tasks = []
        
        # Active tasks
        for task_id, task_obj in self.task_manager.active_tasks.items():
            created_at = task_obj.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            all_tasks.append({
                "task_id": task_id,
                "tool": task_obj.tool_name,
                "status": task_obj.status.value,
                "created_at": task_obj.created_at.isoformat(),
                "age_seconds": int((now_utc - created_at).total_seconds()),
            })
        
        # Completed tasks
        for task_id, task_obj in self.task_manager.completed_results.items():
            created_at = task_obj.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            all_tasks.append({
                "task_id": task_id,
                "tool": task_obj.tool_name,
                "status": task_obj.status.value,
                "created_at": task_obj.created_at.isoformat(),
                "age_seconds": int((now_utc - created_at).total_seconds()),
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
            return f"Task {task_id} cancelled successfully."
        else:
            return f"Failed to cancel task {task_id}."

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
            f"---------------------",
            f"Total tasks: {total_count}",
            f"Active: {active_count}",
            f"Completed: {completed_count}",
        ]

        if status_counts:
            lines.append(f"\nStatus breakdown:")
            for status, count in sorted(status_counts.items()):
                lines.append(f"  {status.upper()}: {count}")

        return "\n".join(lines)


class MCPStatusManager:
    """CLI status manager for MCP server health and metrics."""

    def __init__(self, client_pool: MCPClientPool | None = None) -> None:
        self.client_pool = client_pool or MCPClientPool()
        self._registered_clients: list[Any] = []

    async def initialize_from_config(self, config_file: Path) -> None:
        """Load MCP servers from config YAML and register clients."""
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")

        with open(config_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        mcp_section = raw.get("mcp", {}) or {}
        servers = mcp_section.get("servers", []) or []

        for server in servers:
            server_id = str(server.get("id", "")).strip()
            if not server_id:
                continue

            transport = str(server.get("transport", "http")).strip().lower()
            if transport == "http":
                client = MCPHttpClient(
                    base_url=str(server.get("base_url", "")).strip(),
                    endpoint=str(server.get("endpoint", "/mcp")),
                    timeout_seconds=float(server.get("timeout_seconds", 15.0)),
                    max_retries=int(server.get("max_retries", 2)),
                    retry_backoff_seconds=float(server.get("retry_backoff_seconds", 0.2)),
                )

                def _http_factory(conf: dict[str, Any] = server):
                    return MCPHttpClient(
                        base_url=str(conf.get("base_url", "")).strip(),
                        endpoint=str(conf.get("endpoint", "/mcp")),
                        timeout_seconds=float(conf.get("timeout_seconds", 15.0)),
                        max_retries=int(conf.get("max_retries", 2)),
                        retry_backoff_seconds=float(conf.get("retry_backoff_seconds", 0.2)),
                    )

                self.client_pool.register(server_id, client=client, factory=_http_factory)
                self._registered_clients.append(client)
                continue

            if transport == "stdio":
                command = str(server.get("command", "")).strip()
                args = server.get("args", []) or []
                client = MCPStdioClient(
                    command=command,
                    args=[str(a) for a in args],
                    timeout_seconds=float(server.get("timeout_seconds", 30.0)),
                )

                def _stdio_factory(conf: dict[str, Any] = server):
                    return MCPStdioClient(
                        command=str(conf.get("command", "")).strip(),
                        args=[str(a) for a in (conf.get("args", []) or [])],
                        timeout_seconds=float(conf.get("timeout_seconds", 30.0)),
                    )

                self.client_pool.register(server_id, client=client, factory=_stdio_factory)
                self._registered_clients.append(client)
                continue

            logger.warning("Unknown MCP transport '%s' for server '%s'", transport, server_id)

    async def collect_health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Collect health report for all configured MCP servers."""
        return await self.client_pool.health_check_all()

    async def close(self) -> None:
        """Close all tracked MCP transport clients."""
        for client in self._registered_clients:
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                try:
                    await close_fn()
                except Exception as exc:
                    logger.debug("Ignoring MCP client close error: %s", exc)

    @staticmethod
    def format_snapshot(snapshot: dict[str, dict[str, Any]], as_json: bool = False) -> str:
        """Render MCP health snapshot as json or table."""
        if as_json:
            return json.dumps(snapshot, ensure_ascii=False, indent=2)

        if not snapshot:
            return "No MCP servers configured."

        rows: list[dict[str, Any]] = []
        for server_id, status in snapshot.items():
            metrics = status.get("metrics", {}) if isinstance(status, dict) else {}
            rows.append(
                {
                    "server_id": server_id,
                    "healthy": bool(status.get("healthy", False)) if isinstance(status, dict) else False,
                    "list_calls": metrics.get("list_tools_calls", 0),
                    "tool_calls": metrics.get("call_tool_calls", 0),
                    "errors": metrics.get("errors", 0),
                    "reconnects": metrics.get("reconnects", 0),
                    "latency_ms": f"{float(metrics.get('last_latency_ms', 0.0)):.2f}",
                }
            )

        rows.sort(key=lambda x: x["server_id"])
        return tabulate(rows, headers="keys", tablefmt="grid")


class CLIHeartbeatManager:
    """CLI manager for heartbeat trigger/status operations."""

    def __init__(self, config_file: Path = Path("config.yaml")) -> None:
        self.config_file = config_file

    def _load_config(self):
        return ConfigLoader.load(str(self.config_file))

    async def trigger_once(self) -> dict[str, Any]:
        """Trigger one heartbeat tick and return result payload."""
        config = self._load_config()

        async def _execute(tasks: str) -> dict[str, Any]:
            # S2 scaffold: keep CLI trigger safe and side-effect-free.
            return {
                "status": "noop",
                "tasks_preview": tasks[:200],
                "note": "Heartbeat trigger CLI scaffold; execution adapter lands in subsequent S2/S3 slices.",
            }

        service = HeartbeatService(
            workspace_root=config.workspace_root,
            enabled=config.heartbeat.enabled,
            interval_s=config.heartbeat.interval_s,
            on_execute=_execute,
            notify_enabled=False,
        )

        tick = await service.trigger_now()
        return {
            "action": tick.action,
            "status": tick.status,
            "reason": tick.reason,
            "tasks": tick.tasks,
            "result": tick.result,
        }

    def get_status(self) -> dict[str, Any]:
        """Return heartbeat runtime configuration and file readiness."""
        config = self._load_config()
        heartbeat_file = Path(config.workspace_root) / "HEARTBEAT.md"
        return {
            "enabled": config.heartbeat.enabled,
            "interval_s": config.heartbeat.interval_s,
            "quiet_hours_enabled": config.heartbeat.quiet_hours.enabled,
            "queue_busy_guard_enabled": config.heartbeat.queue_busy_guard.enabled,
            "notify_enabled": config.heartbeat.notify.enabled,
            "heartbeat_file": str(heartbeat_file),
            "heartbeat_file_exists": heartbeat_file.exists(),
            "target_ttl_s": config.heartbeat.routing.target_ttl_s,
        }


class CLICronManager:
    """CLI manager for cron add/list/remove/enable/disable/run-now operations."""

    def __init__(self, config_file: Path = Path("config.yaml")) -> None:
        self.config_file = config_file

    def _load_config(self):
        return ConfigLoader.load(str(self.config_file))

    def _build_service(self) -> CronService:
        config = self._load_config()
        store_path = Path(config.workspace_root) / config.cron.store_file
        return CronService(
            store_file=str(store_path.resolve()),
            enabled=True,
            default_timezone=config.cron.default_timezone,
            on_job=None,
        )

    def add_job(
        self,
        *,
        job_id: str,
        kind: str,
        message: str,
        at_ms: int | None,
        every_ms: int | None,
        expr: str | None,
        tz: str | None,
        deliver: bool,
        channel: str,
        to: str,
    ) -> dict[str, Any]:
        service = self._build_service()
        schedule = CronSchedule(kind=kind, at_ms=at_ms, every_ms=every_ms, expr=expr, tz=tz or "UTC")
        job = service.add_job(
            job_id=job_id,
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            to=to,
        )
        return job.to_dict()

    def list_jobs(self) -> list[dict[str, Any]]:
        service = self._build_service()
        return [job.to_dict() for job in service.list_jobs()]

    def remove_job(self, job_id: str) -> bool:
        service = self._build_service()
        return service.remove_job(job_id)

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        service = self._build_service()
        return service.set_enabled(job_id, enabled)

    async def run_now(self, job_id: str) -> dict[str, Any]:
        async def _noop(job):
            return {
                "status": "noop",
                "job_id": job.id,
                "message_preview": job.message[:200],
            }

        config = self._load_config()
        store_path = Path(config.workspace_root) / config.cron.store_file
        service = CronService(
            store_file=str(store_path.resolve()),
            enabled=True,
            default_timezone=config.cron.default_timezone,
            on_job=_noop,
        )
        result = await service.run_now(job_id)
        return result or {"status": "noop", "job_id": job_id}

