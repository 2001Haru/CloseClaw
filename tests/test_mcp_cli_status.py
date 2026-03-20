"""Tests for MCPStatusManager CLI health/status flow."""

import sys

import pytest

from closeclaw.cli import MCPStatusManager


MOCK_STDIO_SERVER_CODE = """
import json
import sys

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue

    req = json.loads(raw)
    method = req.get('method')
    req_id = req.get('id')

    if method == 'tools/list':
        resp = {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': [
                {
                    'name': 'health_tool',
                    'description': 'health tool',
                    'input_schema': {},
                    'need_auth': False,
                    'tool_type': 'websearch',
                }
            ],
        }
    elif method == 'tools/call':
        resp = {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {'ok': True},
        }
    else:
        resp = {
            'jsonrpc': '2.0',
            'id': req_id,
            'error': 'unknown method',
        }

    sys.stdout.write(json.dumps(resp) + '\\n')
    sys.stdout.flush()
"""


@pytest.mark.asyncio
async def test_mcp_status_manager_collects_snapshot_from_config(tmp_path):
    server_script = tmp_path / "mock_stdio_server.py"
    server_script.write_text(MOCK_STDIO_SERVER_CODE, encoding="utf-8")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
mcp:
  servers:
    - id: local_stdio
      transport: stdio
      command: {sys.executable}
      args:
        - -u
        - {server_script}
      timeout_seconds: 3
""",
        encoding="utf-8",
    )

    manager = MCPStatusManager()
    try:
        await manager.initialize_from_config(config_file)
        snapshot = await manager.collect_health_snapshot()

        assert "local_stdio" in snapshot
        assert snapshot["local_stdio"]["healthy"] is True

        rendered = MCPStatusManager.format_snapshot(snapshot, as_json=False)
        assert "local_stdio" in rendered
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_mcp_status_manager_json_output_shape(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
mcp:
  servers: []
""",
        encoding="utf-8",
    )

    manager = MCPStatusManager()
    try:
        await manager.initialize_from_config(config_file)
        snapshot = await manager.collect_health_snapshot()
        rendered = MCPStatusManager.format_snapshot(snapshot, as_json=True)
        assert rendered.startswith("{")
    finally:
        await manager.close()
