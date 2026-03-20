"""CLI entry point and argument parsing."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .commands import CLITaskManager, MCPStatusManager

logger = logging.getLogger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for CLI commands."""
    parser = argparse.ArgumentParser(
        prog="closeclaw",
        description="CloseClaw agent CLI - Task management interface",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Command: tasks - List all tasks
    tasks_parser = subparsers.add_parser(
        "tasks",
        help="List all tasks (active and completed)",
    )
    tasks_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed information",
    )
    tasks_parser.add_argument(
        "-s", "--state",
        type=str,
        default="state.json",
        help="Path to state.json file (default: state.json)",
    )
    
    # Command: task - Query single task
    task_parser = subparsers.add_parser(
        "task",
        help="Query single task status",
    )
    task_parser.add_argument(
        "task_id",
        type=str,
        help="Task ID (e.g., #001 or 001)",
    )
    task_parser.add_argument(
        "-s", "--state",
        type=str,
        default="state.json",
        help="Path to state.json file",
    )
    
    # Command: cancel - Cancel task
    cancel_parser = subparsers.add_parser(
        "cancel",
        help="Cancel or terminate a task",
    )
    cancel_parser.add_argument(
        "task_id",
        type=str,
        help="Task ID to cancel",
    )
    cancel_parser.add_argument(
        "-s", "--state",
        type=str,
        default="state.json",
        help="Path to state.json file",
    )
    
    # Command: summary - Show task summary
    summary_parser = subparsers.add_parser(
        "summary",
        help="Show task summary",
    )
    summary_parser.add_argument(
        "-s", "--state",
        type=str,
        default="state.json",
        help="Path to state.json file",
    )

    # Command: mcp-health - Show MCP server health/metrics
    mcp_parser = subparsers.add_parser(
        "mcp-health",
        help="Show MCP server health and metrics",
    )
    mcp_parser.add_argument(
        "-c", "--config",
        type=str,
        default="config.yaml",
        help="Path to config.yaml file",
    )
    mcp_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    
    return parser


async def main():
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    # Execute command
    try:
        if args.command in {"tasks", "task", "cancel", "summary"}:
            # Initialize task CLI manager only for task-related commands.
            state_file = Path(args.state)
            cli_manager = CLITaskManager(state_file=state_file)
            await cli_manager.initialize_from_state()

        if args.command == "tasks":
            result = cli_manager.list_tasks(verbose=args.verbose)
            print(result)
            return 0
        
        elif args.command == "task":
            result = cli_manager.get_task_status(args.task_id)
            print(result)
            return 0
        
        elif args.command == "cancel":
            result = await cli_manager.cancel_task(args.task_id)
            print(result)
            return 0
        
        elif args.command == "summary":
            result = cli_manager.show_summary()
            print(result)
            return 0

        elif args.command == "mcp-health":
            mcp_manager = MCPStatusManager()
            try:
                await mcp_manager.initialize_from_config(Path(args.config))
                snapshot = await mcp_manager.collect_health_snapshot()
                print(MCPStatusManager.format_snapshot(snapshot, as_json=args.json))
                return 0
            finally:
                await mcp_manager.close()
        
        else:
            print(f"Unknown command: {args.command}")
            return 1
    
    except Exception as e:
        logger.error(f"Command error: {e}")
        print(f"Error: {e}")
        return 1


def cli():
    """Entry point for console script."""
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nCancelled by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()

