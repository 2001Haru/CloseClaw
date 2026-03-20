"""Tests for need_auth-first permission model and migration helpers."""

import pytest

from closeclaw.types import Tool, ToolType
from closeclaw.middleware import AuthPermissionMiddleware
from closeclaw.config import ConfigLoader
from closeclaw.compatibility import NativeAdapter


class TestAuthPermissionModel:
    @pytest.mark.asyncio
    async def test_need_auth_true_requires_authorization(self, sample_session):
        perms = AuthPermissionMiddleware(default_need_auth=False)
        tool = Tool(
            name="delete_file",
            description="Delete a file",
            type=ToolType.FILE,
            need_auth=True,
        )

        result = await perms.process(tool=tool, arguments={"path": "x"}, session=sample_session)
        assert result["status"] == "requires_auth"

    @pytest.mark.asyncio
    async def test_need_auth_false_allows(self, sample_session):
        perms = AuthPermissionMiddleware(default_need_auth=True)
        tool = Tool(
            name="read_file",
            description="Read a file",
            type=ToolType.FILE,
            need_auth=False,
        )

        result = await perms.process(tool=tool, arguments={"path": "x"}, session=sample_session)
        assert result["status"] == "allow"


class TestMigrationHelpers:
    def test_native_adapter_to_toolspec_v2(self):
        tool = Tool(
            name="shell_exec",
            description="Run command",
            type=ToolType.SHELL,
            need_auth=True,
            parameters={"command": {"type": "string"}},
        )
        spec = NativeAdapter.to_toolspec_v2(tool)
        assert spec.need_auth is True
        assert spec.source == "native"
        assert "exec" in spec.risk_tags


class TestConfigCompatibility:
    def test_config_loader_uses_default_need_auth(self, temp_workspace, tmp_path):
        config_path = tmp_path / "cfg.yaml"
        config_path.write_text(
            f"""
agent_id: test
workspace_root: {temp_workspace}
llm:
  provider: test
  model: test-model
safety:
    default_need_auth: true
""",
            encoding="utf-8",
        )

        cfg = ConfigLoader.load(str(config_path))
        assert cfg.safety.default_need_auth is True




