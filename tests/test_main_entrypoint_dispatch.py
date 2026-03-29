"""Tests for python -m closeclaw entrypoint dispatch behavior."""

import types

import pytest

from closeclaw import __main__ as entry


def test_dispatches_cli_for_cli_subcommands(monkeypatch):
    called = {"cli": 0, "runner": 0}

    fake_cli_module = types.SimpleNamespace(cli=lambda: called.__setitem__("cli", called["cli"] + 1))
    fake_runner_module = types.SimpleNamespace(main=lambda: called.__setitem__("runner", called["runner"] + 1))
    monkeypatch.setitem(__import__("sys").modules, "closeclaw.cli.main", fake_cli_module)
    monkeypatch.setitem(__import__("sys").modules, "closeclaw.runner", fake_runner_module)
    monkeypatch.setattr("sys.argv", ["python", "mcp-health", "--config", "config.yaml"])

    entry.main()

    assert called["cli"] == 1
    assert called["runner"] == 0


def test_dispatches_runner_for_non_cli_subcommands(monkeypatch):
    called = {"cli": 0, "runner": 0}

    fake_cli_module = types.SimpleNamespace(cli=lambda: called.__setitem__("cli", called["cli"] + 1))
    fake_runner_module = types.SimpleNamespace(main=lambda: called.__setitem__("runner", called["runner"] + 1))
    monkeypatch.setitem(__import__("sys").modules, "closeclaw.cli.main", fake_cli_module)
    monkeypatch.setitem(__import__("sys").modules, "closeclaw.runner", fake_runner_module)
    monkeypatch.setattr("sys.argv", ["python", "--config", "config.yaml"])

    entry.main()

    assert called["cli"] == 0
    assert called["runner"] == 1


@pytest.mark.parametrize(
    "subcmd",
    [
        "agent",
        "gateway",
        "tasks",
        "list",
        "task",
        "show",
        "cancel",
        "stop",
        "summary",
        "mcp-health",
        "mcp",
        "channel-health",
        "channel",
        "provider-health",
        "provider",
        "runtime-health",
        "runtime",
        "heartbeat-trigger",
        "heartbeat-status",
        "cron-add",
        "cron-list",
        "cron-remove",
        "cron-enable",
        "cron-disable",
        "cron-run-now",
    ],
)
def test_all_known_cli_subcommands_dispatch_to_cli(monkeypatch, subcmd):
    called = {"cli": 0, "runner": 0}

    fake_cli_module = types.SimpleNamespace(cli=lambda: called.__setitem__("cli", called["cli"] + 1))
    fake_runner_module = types.SimpleNamespace(main=lambda: called.__setitem__("runner", called["runner"] + 1))
    monkeypatch.setitem(__import__("sys").modules, "closeclaw.cli.main", fake_cli_module)
    monkeypatch.setitem(__import__("sys").modules, "closeclaw.runner", fake_runner_module)
    monkeypatch.setattr("sys.argv", ["python", subcmd])

    entry.main()

    assert called["cli"] == 1
    assert called["runner"] == 0
