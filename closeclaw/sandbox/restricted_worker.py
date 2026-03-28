"""Restricted child worker for executing shell commands."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def _decode_b64(value: str) -> str:
    return base64.b64decode(value.encode("ascii")).decode("utf-8", errors="replace")


def _load_env(env_file: str | None) -> dict[str, str] | None:
    if not env_file:
        return None
    path = Path(env_file)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    return {str(k): str(v) for k, v in data.items()}


def _write_output(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="CloseClaw restricted shell worker")
    parser.add_argument("--command-b64", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--cwd", default="")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    command = _decode_b64(args.command_b64)
    cwd = args.cwd or None
    env = _load_env(args.env_file or None)

    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(1, int(args.timeout)),
            cwd=cwd,
            env=env if env is not None else os.environ.copy(),
        )
        _write_output(
            args.output,
            {
                "returncode": int(completed.returncode),
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "executed": True,
                "sandbox_backend": "windows_restricted_token",
            },
        )
        return 0
    except subprocess.TimeoutExpired:
        _write_output(
            args.output,
            {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {args.timeout} seconds",
                "executed": False,
                "sandbox_backend": "windows_restricted_token",
            },
        )
        return 124
    except Exception as exc:
        _write_output(
            args.output,
            {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "executed": False,
                "sandbox_backend": "windows_restricted_token",
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

