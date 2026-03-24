"""Tests for CLI commands (Phase 2)."""

import pytest
import asyncio
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from closeclaw.cli import CLITaskManager
from closeclaw.types import BackgroundTask, TaskStatus


class TestCLITaskManager:
    """Test cases for CLI task management."""
    
    @pytest.fixture
    def temp_state_file(self):
        """Create temporary state.json file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = Path(f.name)
        yield temp_path
        # Cleanup
        if temp_path.exists():
            temp_path.unlink()
    
    @pytest.fixture
    def cli_manager(self, temp_state_file):
        """Create CLI manager with temp state."""
        return CLITaskManager(state_file=temp_state_file)
    
    def test_cli_manager_initialization(self, cli_manager):
        """Test: CLI manager initializes correctly."""
        assert cli_manager.task_manager is not None
        assert len(cli_manager.task_manager.completed_results) == 0
        assert len(cli_manager.task_manager.active_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, cli_manager):
        """Test: List tasks when none exist."""
        result = cli_manager.list_tasks()
        assert "No tasks found" in result
    
    @pytest.mark.asyncio
    async def test_list_tasks_with_data(self, cli_manager):
        """Test: List tasks with completed task data."""
        # Create mock task
        task = BackgroundTask(
            task_id="#001",
            tool_name="web_search",
            tool_arguments={"query": "test"},
            expires_after=3600,
        )
        task.status = TaskStatus.COMPLETED
        task.result = {"results": ["item1", "item2"]}
        task.completed_at = datetime.now(timezone.utc)
        
        # Add to manager
        cli_manager.task_manager.completed_results["#001"] = task
        
        # List
        result = cli_manager.list_tasks()
        
        assert "#001" in result
        assert "web_search" in result
        assert "completed" in result.lower()
    
    @pytest.mark.asyncio
    async def test_get_task_status_found(self, cli_manager):
        """Test: Get status for existing task."""
        task = BackgroundTask(
            task_id="#001",
            tool_name="web_search",
            tool_arguments={"query": "python"},
            expires_after=3600,
        )
        task.status = TaskStatus.COMPLETED
        task.result = {"count": 42}
        task.started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        task.completed_at = datetime.now(timezone.utc)
        
        cli_manager.task_manager.completed_results["#001"] = task
        
        result = cli_manager.get_task_status("#001")
        
        assert "Task ID: #001" in result
        assert "web_search" in result
        assert "python" in result
        assert "42" in result
    
    @pytest.mark.asyncio
    async def test_get_task_status_not_found(self, cli_manager):
        """Test: Get status for non-existent task."""
        result = cli_manager.get_task_status("#999")
        assert "not found" in result.lower()
    
    @pytest.mark.asyncio
    async def test_cancel_task_not_running(self, cli_manager):
        """Test: Cannot cancel completed task."""
        task = BackgroundTask(
            task_id="#001",
            tool_name="web_search",
            tool_arguments={},
            expires_after=3600,
        )
        task.status = TaskStatus.COMPLETED
        
        cli_manager.task_manager.completed_results["#001"] = task
        
        result = await cli_manager.cancel_task("#001")
        
        assert "not running" in result.lower()
    
    @pytest.mark.asyncio
    async def test_cancel_task_not_found(self, cli_manager):
        """Test: Cancel non-existent task."""
        result = await cli_manager.cancel_task("#999")
        assert "not found" in result.lower()
    
    def test_show_summary(self, cli_manager):
        """Test: Show task summary."""
        # Add some tasks
        for i in range(3):
            task = BackgroundTask(
                task_id=f"#{i:03d}",
                tool_name="web_search",
                tool_arguments={},
                expires_after=3600,
            )
            task.status = TaskStatus.COMPLETED if i < 2 else TaskStatus.RUNNING
            if task.status == TaskStatus.RUNNING:
                cli_manager.task_manager.active_tasks[f"#{i:03d}"] = task
            else:
                cli_manager.task_manager.completed_results[f"#{i:03d}"] = task
        
        result = cli_manager.show_summary()
        
        assert "Total tasks: 3" in result
        assert "Active: 1" in result
        assert "Completed: 2" in result
        assert "COMPLETED: 2" in result or "completed: 2" in result.lower()
    
    def test_load_state_file_not_found(self, cli_manager):
        """Test: Load state when file doesn't exist."""
        cli_manager.state_file = Path("/nonexistent/state.json")
        result = cli_manager.load_state()
        assert result == {}
    
    @pytest.mark.asyncio
    async def test_load_state_from_file(self, cli_manager, temp_state_file):
        """Test: Load state from saved file."""
        # Create state data
        state_data = {
            "completed_results": {
                "#001": {
                    "task_id": "#001",
                    "tool_name": "web_search",
                    "tool_arguments": {"query": "test"},
                    "status": "completed",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "started_at": None,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "result": {"count": 5},
                    "error": None,
                    "expires_after": 3600,
                }
            },
            "active_tasks": {},
        }
        
        # Write to file
        with open(temp_state_file, 'w') as f:
            json.dump(state_data, f)
        
        # Load
        cli_manager.state_file = temp_state_file
        await cli_manager.initialize_from_state()
        
        # Verify
        assert "#001" in cli_manager.task_manager.completed_results
        task = cli_manager.task_manager.completed_results["#001"]
        assert task.tool_name == "web_search"
        assert task.result == {"count": 5}


class TestCLIArgumentParsing:
    """Test CLI argument parsing."""
    
    def test_parser_tasks_command(self):
        """Test: Parse 'tasks' command."""
        from closeclaw.cli import create_parser
        
        parser = create_parser()
        args = parser.parse_args(["tasks", "-v"])
        
        assert args.command == "tasks"
        assert args.verbose is True

    def test_parser_agent_command(self):
        """Test: Parse 'agent' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["agent", "--config", "config.yaml"])

        assert args.command == "agent"
        assert args.config == "config.yaml"

    def test_parser_gateway_command(self):
        """Test: Parse 'gateway' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["gateway", "--config", "config.yaml"])

        assert args.command == "gateway"
        assert args.config == "config.yaml"

    def test_parser_list_show_stop_aliases(self):
        """Test: Parse concise aliases list/show/stop."""
        from closeclaw.cli import create_parser

        parser = create_parser()

        args = parser.parse_args(["list", "-v"])
        assert args.command == "list"
        assert args.verbose is True

        args = parser.parse_args(["show", "#001"])
        assert args.command == "show"
        assert args.task_id == "#001"

        args = parser.parse_args(["stop", "#001"])
        assert args.command == "stop"
        assert args.task_id == "#001"
    
    def test_parser_task_command(self):
        """Test: Parse 'task' command."""
        from closeclaw.cli import create_parser
        
        parser = create_parser()
        args = parser.parse_args(["task", "#001"])
        
        assert args.command == "task"
        assert args.task_id == "#001"
    
    def test_parser_cancel_command(self):
        """Test: Parse 'cancel' command."""
        from closeclaw.cli import create_parser
        
        parser = create_parser()
        args = parser.parse_args(["cancel", "#001"])
        
        assert args.command == "cancel"
        assert args.task_id == "#001"
    
    def test_parser_summary_command(self):
        """Test: Parse 'summary' command."""
        from closeclaw.cli import create_parser
        
        parser = create_parser()
        args = parser.parse_args(["summary"])
        
        assert args.command == "summary"

    def test_parser_mcp_health_command(self):
        """Test: Parse 'mcp-health' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["mcp-health", "--config", "config.yaml", "--json"])

        assert args.command == "mcp-health"
        assert args.config == "config.yaml"
        assert args.json is True

    def test_parser_channel_health_command(self):
        """Test: Parse 'channel-health' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["channel-health", "--config", "config.yaml", "--name", "discord", "--json"])

        assert args.command == "channel-health"
        assert args.config == "config.yaml"
        assert args.name == "discord"
        assert args.json is True

    def test_parser_provider_health_command(self):
        """Test: Parse 'provider-health' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["provider-health", "--config", "config.yaml", "--name", "openai"])

        assert args.command == "provider-health"
        assert args.config == "config.yaml"
        assert args.name == "openai"

    def test_parser_mcp_channel_provider_alias_commands(self):
        """Test: Parse concise aliases mcp/channel/provider."""
        from closeclaw.cli import create_parser

        parser = create_parser()

        args = parser.parse_args(["mcp", "--json"])
        assert args.command == "mcp"
        assert args.json is True

        args = parser.parse_args(["channel", "--name", "discord"])
        assert args.command == "channel"
        assert args.name == "discord"

        args = parser.parse_args(["provider", "--name", "openai"])
        assert args.command == "provider"
        assert args.name == "openai"

    def test_parser_heartbeat_trigger_command(self):
        """Test: Parse 'heartbeat-trigger' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["heartbeat-trigger", "--config", "config.yaml", "--json"])

        assert args.command == "heartbeat-trigger"
        assert args.config == "config.yaml"
        assert args.json is True

    def test_parser_heartbeat_status_command(self):
        """Test: Parse 'heartbeat-status' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["heartbeat-status", "--config", "config.yaml"])

        assert args.command == "heartbeat-status"
        assert args.config == "config.yaml"

    def test_parser_cron_add_command(self):
        """Test: Parse 'cron-add' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args([
            "cron-add",
            "--id", "job1",
            "--kind", "every",
            "--message", "hello",
            "--every-ms", "1000",
            "--config", "config.yaml",
        ])

        assert args.command == "cron-add"
        assert args.id == "job1"
        assert args.kind == "every"
        assert args.every_ms == 1000

    def test_parser_cron_list_command(self):
        """Test: Parse 'cron-list' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["cron-list", "--config", "config.yaml", "--json"])

        assert args.command == "cron-list"
        assert args.config == "config.yaml"
        assert args.json is True

    def test_parser_cron_remove_command(self):
        """Test: Parse 'cron-remove' command."""
        from closeclaw.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["cron-remove", "job1", "--config", "config.yaml"])

        assert args.command == "cron-remove"
        assert args.id == "job1"

    def test_parser_cron_enable_disable_and_run_now_commands(self):
        """Test: Parse cron enable/disable/run-now commands."""
        from closeclaw.cli import create_parser

        parser = create_parser()

        args = parser.parse_args(["cron-enable", "job1"])
        assert args.command == "cron-enable"
        assert args.id == "job1"

        args = parser.parse_args(["cron-disable", "job1"])
        assert args.command == "cron-disable"
        assert args.id == "job1"

        args = parser.parse_args(["cron-run-now", "job1", "--json"])
        assert args.command == "cron-run-now"
        assert args.id == "job1"
        assert args.json is True


class TestCLIIntegration:
    """Integration tests with real TaskManager."""
    
    @pytest.mark.asyncio
    async def test_cli_complete_workflow(self):
        """Test: Complete CLI workflow."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = Path(f.name)
        
        try:
            cli_manager = CLITaskManager(state_file=temp_path)
            
            # Create multi task
            t1 = await cli_manager.task_manager.create_task("web_search", {"query": "python"})
            t2 = await cli_manager.task_manager.create_task("file_read", {"path": "test.txt"})
            
            # List tasks
            list_result = cli_manager.list_tasks()
            assert t1 in list_result
            assert t2 in list_result
            
            # Get status
            status_result = cli_manager.get_task_status(t1)
            assert t1 in status_result
            assert "web_search" in status_result
            
            # Summary
            summary = cli_manager.show_summary()
            assert "Total tasks: 2" in summary
        
        finally:
            if temp_path.exists():
                temp_path.unlink()


class TestCLIHealthManagers:
    """Tests for channel/provider health CLI managers."""

    @pytest.fixture
    def temp_config_file(self):
        content = """
agent_id: "test-agent"
workspace_root: "."
llm:
  provider: "openai"
  model: "gpt-4"
  api_key: "test-key"
channels:
  - type: "cli"
    enabled: true
  - type: "discord"
    enabled: false
    token: ""
safety:
  admin_user_ids: ["cli_user"]
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            p = Path(f.name)
        yield p
        if p.exists():
            p.unlink()

    def test_channel_health_manager_snapshot(self, temp_config_file):
        from closeclaw.cli.commands import CLIChannelHealthManager

        manager = CLIChannelHealthManager(config_file=temp_config_file)
        snapshot = manager.get_health()

        assert "summary" in snapshot
        assert "channels" in snapshot
        assert any(item["channel"] == "cli" for item in snapshot["channels"])

    def test_provider_health_manager_snapshot(self, temp_config_file):
        from closeclaw.cli.commands import CLIProviderHealthManager

        manager = CLIProviderHealthManager(config_file=temp_config_file)
        snapshot = manager.get_health()

        assert "summary" in snapshot
        assert "providers" in snapshot
        assert snapshot["summary"]["active_provider"] in {"openai", "openai-compatible"}

    def test_provider_health_manager_named_ollama(self, temp_config_file):
        from closeclaw.cli.commands import CLIProviderHealthManager

        manager = CLIProviderHealthManager(config_file=temp_config_file)
        snapshot = manager.get_health("ollama")

        assert snapshot["summary"]["total"] == 1
        assert snapshot["providers"][0]["provider"] == "ollama"
        assert snapshot["providers"][0]["runtime"] == "ollama"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])





