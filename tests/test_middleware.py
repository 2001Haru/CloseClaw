"""Tests for middleware system."""

import pytest
import os
from datetime import datetime
from pathlib import Path

from closeclaw.types import Zone, ToolType, Tool, Session
from closeclaw.middleware import SafetyGuard, PathSandbox, ZoneBasedPermission, MiddlewareChain


class TestSafetyGuard:
    """Test SafetyGuard middleware."""
    
    @pytest.mark.asyncio
    async def test_allow_safe_command(self):
        """Test allowing safe shell commands."""
        guard = SafetyGuard()
        tool = Tool(
            name="ls",
            description="List files",
            zone=Zone.ZONE_A,
            type=ToolType.SHELL
        )
        
        result = await guard.process(
            tool=tool,
            arguments={"command": "ls -la /home/user"},
            session=None
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_block_recursive_delete_windows(self):
        """Test blocking dangerous recursive delete on Windows."""
        guard = SafetyGuard()
        tool = Tool(
            name="del",
            description="Delete files",
            zone=Zone.ZONE_C,
            type=ToolType.SHELL
        )
        
        result = await guard.process(
            tool=tool,
            arguments={"command": "del /s C:\\important\\data"},
            session=None
        )
        
        assert result["status"] == "block"
        assert "dangerous" in result["reason"].lower()
    
    @pytest.mark.asyncio
    async def test_block_recursive_rm_unix(self):
        """Test blocking dangerous rm -rf on Unix."""
        guard = SafetyGuard()
        tool = Tool(
            name="rm",
            description="Delete files",
            zone=Zone.ZONE_C,
            type=ToolType.SHELL
        )
        
        result = await guard.process(
            tool=tool,
            arguments={"command": "rm -rf /home/user/*"},
            session=None
        )
        
        assert result["status"] == "block"
    
    @pytest.mark.asyncio
    async def test_block_sudo_rm_rf(self):
        """Test blocking sudo rm -rf commands."""
        guard = SafetyGuard()
        tool = Tool(
            name="rm",
            description="Delete files",
            zone=Zone.ZONE_C,
            type=ToolType.SHELL
        )
        
        result = await guard.process(
            tool=tool,
            arguments={"command": "sudo rm -rf /"},
            session=None
        )
        
        assert result["status"] == "block"
    
    @pytest.mark.asyncio
    async def test_ignore_non_shell_tools(self):
        """Test SafetyGuard ignores non-shell tools."""
        guard = SafetyGuard()
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        result = await guard.process(
            tool=tool,
            arguments={"path": "/etc/passwd"},
            session=None
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_custom_blacklist_rules(self):
        """Test SafetyGuard with custom rules."""
        guard = SafetyGuard(custom_rules=[r"dropdb\s+", r"truncate\s+"])
        tool = Tool(
            name="psql",
            description="PostgreSQL command",
            zone=Zone.ZONE_C,
            type=ToolType.SHELL
        )
        
        result = await guard.process(
            tool=tool,
            arguments={"command": "dropdb production_db"},
            session=None
        )
        
        assert result["status"] == "block"


class TestPathSandbox:
    """Test PathSandbox middleware."""
    
    @pytest.mark.asyncio
    async def test_allow_file_in_workspace(self, temp_workspace):
        """Test allowing file operations within workspace."""
        sandbox = PathSandbox(temp_workspace)
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        test_file = Path(temp_workspace) / "test.txt"
        test_file.write_text("test content")
        
        result = await sandbox.process(
            tool=tool,
            arguments={"path": str(test_file)},
            session=None
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_block_file_outside_workspace(self, temp_workspace):
        """Test blocking file operations outside workspace."""
        sandbox = PathSandbox(temp_workspace)
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        
        # Try to access file outside workspace
        result = await sandbox.process(
            tool=tool,
            arguments={"path": "/etc/passwd"},
            session=None
        )
        
        assert result["status"] == "block"
        assert "outside" in result["reason"].lower() or "not allowed" in result["reason"].lower()
    
    @pytest.mark.asyncio
    async def test_allow_symlink_within_workspace(self, temp_workspace):
        """Test allowing symlinks within workspace."""
        sandbox = PathSandbox(temp_workspace)
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        # Create a file and symlink within workspace
        real_file = Path(temp_workspace) / "real.txt"
        real_file.write_text("content")
        link_file = Path(temp_workspace) / "link.txt"
        try:
            link_file.symlink_to(real_file)
        except (OSError, NotImplementedError):
            # Skip if symlinks not supported (e.g., Windows without admin)
            pytest.skip("Symlinks not supported on this system")
        
        result = await sandbox.process(
            tool=tool,
            arguments={"path": str(link_file)},
            session=None
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_block_path_traversal(self, temp_workspace):
        """Test blocking path traversal attacks."""
        sandbox = PathSandbox(temp_workspace)
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        # Try path traversal
        result = await sandbox.process(
            tool=tool,
            arguments={"path": os.path.join(temp_workspace, "..", "..", "etc", "passwd")},
            session=None
        )
        
        assert result["status"] == "block"


class TestZoneBasedPermission:
    """Test ZoneBasedPermission middleware."""
    
    @pytest.mark.asyncio
    async def test_zone_a_auto_approve(self, sample_session):
        """Test Zone A operations are auto-approved."""
        perms = ZoneBasedPermission(admin_user_id="admin_001")
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        result = await perms.process(
            tool=tool,
            arguments={"path": "/data/public.txt"},
            session=sample_session
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_zone_b_silent_log(self, sample_session):
        """Test Zone B operations are logged silently."""
        perms = ZoneBasedPermission(admin_user_id="admin_001")
        tool = Tool(
            name="log_event",
            description="Log event",
            zone=Zone.ZONE_B,
            type=ToolType.FILE
        )
        
        result = await perms.process(
            tool=tool,
            arguments={"event": "user_login"},
            session=sample_session
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_zone_c_require_auth(self, sample_session):
        """Test Zone C operations require authorization."""
        perms = ZoneBasedPermission(admin_user_id="admin_001")
        tool = Tool(
            name="delete_file",
            description="Delete file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        
        result = await perms.process(
            tool=tool,
            arguments={"path": "/data/important.txt"},
            session=sample_session
        )
        
        assert result["status"] == "requires_auth"
        assert "auth_request" in result
    
    @pytest.mark.asyncio
    async def test_auth_request_structure(self, sample_session):
        """Test auth request has proper structure."""
        perms = ZoneBasedPermission(admin_user_id="admin_001")
        tool = Tool(
            name="delete_file",
            description="Delete file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        
        result = await perms.process(
            tool=tool,
            arguments={"path": "/data/critical.txt"},
            session=sample_session
        )
        
        assert "auth_request" in result
        auth_req = result["auth_request"]
        assert "id" in auth_req
        assert auth_req["tool_name"] == "delete_file"
        assert auth_req["user_id"] == sample_session.user_id


class TestMiddlewareChain:
    """Test MiddlewareChain execution."""
    
    @pytest.mark.asyncio
    async def test_chain_all_pass(self, temp_workspace, sample_session):
        """Test chain when all middleware pass."""
        middlewares = [
            SafetyGuard(),
            PathSandbox(temp_workspace),
            ZoneBasedPermission()
        ]
        chain = MiddlewareChain(middlewares)
        
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        test_file = Path(temp_workspace) / "test.txt"
        test_file.write_text("content")
        
        result = await chain.execute(
            tool=tool,
            arguments={"path": str(test_file)},
            session=sample_session
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_chain_stops_on_block(self, temp_workspace, sample_session):
        """Test chain stops when middleware blocks."""
        middlewares = [
            SafetyGuard(),
            PathSandbox(temp_workspace),
        ]
        chain = MiddlewareChain(middlewares)
        
        tool = Tool(
            name="shell",
            description="Shell command",
            zone=Zone.ZONE_C,
            type=ToolType.SHELL
        )
        
        result = await chain.execute(
            tool=tool,
            arguments={"command": "rm -rf /"},
            session=sample_session
        )
        
        # Should be blocked by SafetyGuard
        assert result["status"] == "block"
    
    @pytest.mark.asyncio
    async def test_chain_accumulates_metadata(self, temp_workspace, sample_session):
        """Test chain accumulates metadata from middleware."""
        middlewares = [
            SafetyGuard(),
            PathSandbox(temp_workspace),
            ZoneBasedPermission()
        ]
        chain = MiddlewareChain(middlewares)
        
        tool = Tool(
            name="delete_file",
            description="Delete file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        
        delete_file = Path(temp_workspace) / "target.txt"
        delete_file.write_text("delete me")
        
        result = await chain.execute(
            tool=tool,
            arguments={"path": str(delete_file)},
            session=sample_session
        )
        
        # Should reach auth requirement (not blocked earlier)
        assert result["status"] == "requires_auth"


class TestMiddlewareIntegration:
    """Integration tests for middleware system."""
    
    @pytest.mark.asyncio
    async def test_realistic_file_read_scenario(self, temp_workspace, sample_session):
        """Test realistic file read scenario."""
        middlewares = [
            SafetyGuard(),
            PathSandbox(temp_workspace),
            ZoneBasedPermission()
        ]
        chain = MiddlewareChain(middlewares)
        
        # Create test file
        test_dir = Path(temp_workspace) / "data"
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "config.json"
        test_file.write_text('{"api_key": "secret"}')
        
        tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        result = await chain.execute(
            tool=tool,
            arguments={"path": str(test_file)},
            session=sample_session
        )
        
        assert result["status"] == "allow"
    
    @pytest.mark.asyncio
    async def test_realistic_dangerous_deletion(self, temp_workspace, sample_session):
        """Test realistic dangerous deletion scenario."""
        middlewares = [
            SafetyGuard(),
            PathSandbox(temp_workspace),
            ZoneBasedPermission(admin_user_id="admin_001")
        ]
        chain = MiddlewareChain(middlewares)
        
        # Create file to delete
        delete_file = Path(temp_workspace) / "important.txt"
        delete_file.write_text("important data")
        
        tool = Tool(
            name="delete_file",
            description="Delete file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        
        result = await chain.execute(
            tool=tool,
            arguments={"path": str(delete_file)},
            session=sample_session
        )
        
        # Should require auth
        assert result["status"] == "requires_auth"
        assert "auth_request" in result
