"""CloseClaw runner entry point.

Allows running with: python -m closeclaw
"""

from __future__ import annotations

import sys


CLI_SUBCOMMANDS = {
    "tasks",
    "task",
    "cancel",
    "summary",
    "mcp-health",
    "heartbeat-trigger",
    "heartbeat-status",
    "cron-add",
    "cron-list",
    "cron-remove",
    "cron-enable",
    "cron-disable",
    "cron-run-now",
}


def main() -> None:
    """Dispatch to runner or CLI entrypoint based on first subcommand."""
    first_arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if first_arg in CLI_SUBCOMMANDS:
        from .cli.main import cli as cli_main

        cli_main()
        return

    from .runner import main as runner_main

    runner_main()

if __name__ == "__main__":
    main()

