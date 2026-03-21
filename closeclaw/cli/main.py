"""CLI entry point and argument parsing."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .commands import CLITaskManager, MCPStatusManager, CLIHeartbeatManager, CLICronManager

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

    # Command: heartbeat-trigger - Trigger one heartbeat tick immediately
    hb_trigger_parser = subparsers.add_parser(
        "heartbeat-trigger",
        help="Trigger one heartbeat tick immediately",
    )
    hb_trigger_parser.add_argument(
        "-c", "--config",
        type=str,
        default="config.yaml",
        help="Path to config.yaml file",
    )
    hb_trigger_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    # Command: heartbeat-status - Show heartbeat config/status summary
    hb_status_parser = subparsers.add_parser(
        "heartbeat-status",
        help="Show heartbeat configuration and readiness status",
    )
    hb_status_parser.add_argument(
        "-c", "--config",
        type=str,
        default="config.yaml",
        help="Path to config.yaml file",
    )
    hb_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    cron_add_parser = subparsers.add_parser(
        "cron-add",
        help="Add a cron job",
    )
    cron_add_parser.add_argument("--id", required=True, type=str, help="Cron job id")
    cron_add_parser.add_argument("--kind", required=True, choices=["at", "every", "cron"], help="Schedule kind")
    cron_add_parser.add_argument("--message", required=True, type=str, help="Task message for this cron job")
    cron_add_parser.add_argument("--at-ms", type=int, default=None, help="Absolute epoch milliseconds for at schedule")
    cron_add_parser.add_argument("--every-ms", type=int, default=None, help="Interval milliseconds for every schedule")
    cron_add_parser.add_argument("--expr", type=str, default=None, help="Cron expression for cron schedule")
    cron_add_parser.add_argument("--tz", type=str, default="UTC", help="Timezone for cron schedule")
    cron_add_parser.add_argument("--deliver", action="store_true", help="Deliver to channel instead of local run")
    cron_add_parser.add_argument("--channel", type=str, default="cli", help="Target channel")
    cron_add_parser.add_argument("--to", type=str, default="direct", help="Target chat or recipient")
    cron_add_parser.add_argument("-c", "--config", type=str, default="config.yaml", help="Path to config.yaml file")
    cron_add_parser.add_argument("--json", action="store_true", help="Output as JSON")

    cron_list_parser = subparsers.add_parser(
        "cron-list",
        help="List cron jobs",
    )
    cron_list_parser.add_argument("-c", "--config", type=str, default="config.yaml", help="Path to config.yaml file")
    cron_list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    cron_remove_parser = subparsers.add_parser(
        "cron-remove",
        help="Remove a cron job by id",
    )
    cron_remove_parser.add_argument("id", type=str, help="Cron job id")
    cron_remove_parser.add_argument("-c", "--config", type=str, default="config.yaml", help="Path to config.yaml file")

    cron_enable_parser = subparsers.add_parser(
        "cron-enable",
        help="Enable a cron job by id",
    )
    cron_enable_parser.add_argument("id", type=str, help="Cron job id")
    cron_enable_parser.add_argument("-c", "--config", type=str, default="config.yaml", help="Path to config.yaml file")

    cron_disable_parser = subparsers.add_parser(
        "cron-disable",
        help="Disable a cron job by id",
    )
    cron_disable_parser.add_argument("id", type=str, help="Cron job id")
    cron_disable_parser.add_argument("-c", "--config", type=str, default="config.yaml", help="Path to config.yaml file")

    cron_run_now_parser = subparsers.add_parser(
        "cron-run-now",
        help="Run a cron job immediately by id",
    )
    cron_run_now_parser.add_argument("id", type=str, help="Cron job id")
    cron_run_now_parser.add_argument("-c", "--config", type=str, default="config.yaml", help="Path to config.yaml file")
    cron_run_now_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
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

        elif args.command == "heartbeat-trigger":
            hb_manager = CLIHeartbeatManager(config_file=Path(args.config))
            result = await hb_manager.trigger_once()
            if args.json:
                import json
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"action={result.get('action')} status={result.get('status')} reason={result.get('reason')}")
                if result.get("tasks"):
                    print(f"tasks={result.get('tasks')[:200]}")
            return 0

        elif args.command == "heartbeat-status":
            hb_manager = CLIHeartbeatManager(config_file=Path(args.config))
            status = hb_manager.get_status()
            if args.json:
                import json
                print(json.dumps(status, ensure_ascii=False, indent=2))
            else:
                print(
                    "enabled={enabled} interval_s={interval_s} file_exists={heartbeat_file_exists} "
                    "notify_enabled={notify_enabled} target_ttl_s={target_ttl_s}".format(**status)
                )
                print(f"heartbeat_file={status.get('heartbeat_file')}")
            return 0

        elif args.command == "cron-add":
            cron = CLICronManager(config_file=Path(args.config))
            job = cron.add_job(
                job_id=args.id,
                kind=args.kind,
                message=args.message,
                at_ms=args.at_ms,
                every_ms=args.every_ms,
                expr=args.expr,
                tz=args.tz,
                deliver=args.deliver,
                channel=args.channel,
                to=args.to,
            )
            if args.json:
                import json
                print(json.dumps(job, ensure_ascii=False, indent=2))
            else:
                print(f"added id={job.get('id')} kind={job.get('schedule', {}).get('kind')} enabled={job.get('enabled')}")
            return 0

        elif args.command == "cron-list":
            cron = CLICronManager(config_file=Path(args.config))
            jobs = cron.list_jobs()
            if args.json:
                import json
                print(json.dumps(jobs, ensure_ascii=False, indent=2))
            else:
                print(f"count={len(jobs)}")
                for j in jobs:
                    print(
                        "id={id} enabled={enabled} kind={kind} next={next_run_at_ms}".format(
                            id=j.get("id"),
                            enabled=j.get("enabled"),
                            kind=(j.get("schedule") or {}).get("kind"),
                            next_run_at_ms=(j.get("state") or {}).get("next_run_at_ms"),
                        )
                    )
            return 0

        elif args.command == "cron-remove":
            cron = CLICronManager(config_file=Path(args.config))
            ok = cron.remove_job(args.id)
            print(f"removed={ok} id={args.id}")
            return 0 if ok else 1

        elif args.command == "cron-enable":
            cron = CLICronManager(config_file=Path(args.config))
            ok = cron.set_enabled(args.id, True)
            print(f"enabled={ok} id={args.id}")
            return 0 if ok else 1

        elif args.command == "cron-disable":
            cron = CLICronManager(config_file=Path(args.config))
            ok = cron.set_enabled(args.id, False)
            print(f"disabled={ok} id={args.id}")
            return 0 if ok else 1

        elif args.command == "cron-run-now":
            cron = CLICronManager(config_file=Path(args.config))
            result = await cron.run_now(args.id)
            if args.json:
                import json
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"status={result.get('status')} job_id={result.get('job_id')}")
            return 0
        
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

