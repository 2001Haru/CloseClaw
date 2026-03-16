"""Tests for tools system."""

import pytest
import os
from pathlib import Path

from closeclaw.tools.base import get_registered_tools
from closeclaw.types import ToolType, Zone


class TestFileTools:
    """Test file operation tools."""
    
    @pytest.mark.asyncio
    async def test_read_file_tool(self, temp_workspace):
        # Create test file
        test_file = Path(temp_workspace) / "test.txt"
        test_file.write_text("Hello, World!")
        
        # Test direct function
        from closeclaw.tools.file_tools import read_file_impl
        content = await read_file_impl(str(test_file))
        assert content == "Hello, World!"
        
        # Test metadata
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "read_file"), None)
        assert tool is not None
        assert tool.zone == Zone.ZONE_A
        assert tool.type == ToolType.FILE

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, temp_workspace):
        from closeclaw.tools.file_tools import read_file_impl
        with pytest.raises(Exception):
            await read_file_impl(str(Path(temp_workspace) / "nonexistent.txt"))

    @pytest.mark.asyncio
    async def test_write_file_tool(self, temp_workspace):
        test_file = Path(temp_workspace) / "output.txt"
        
        from closeclaw.tools.file_tools import write_file_impl
        result = await write_file_impl(str(test_file), "Test content")
        
        assert "File written" in result
        assert test_file.exists()
        assert test_file.read_text() == "Test content"
        
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "write_file"), None)
        assert tool.zone == Zone.ZONE_C

    @pytest.mark.asyncio
    async def test_append_file_tool(self, temp_workspace):
        test_file = Path(temp_workspace) / "append.txt"
        test_file.write_text("Line 1\n")
        
        from closeclaw.tools.file_tools import append_file_impl
        result = await append_file_impl(str(test_file), "Line 2")
        
        assert "Content appended" in result
        content = test_file.read_text()
        assert "Line 1" in content
        assert "Line 2" in content

    @pytest.mark.asyncio
    async def test_delete_file_tool(self, temp_workspace):
        test_file = Path(temp_workspace) / "delete_me.txt"
        test_file.write_text("junk")
        
        from closeclaw.tools.file_tools import delete_file_impl
        result = await delete_file_impl(str(test_file))
        
        assert "File deleted" in result
        assert not test_file.exists()
        
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "delete_file"), None)
        assert tool.zone == Zone.ZONE_C  # Delete is Zone C

    @pytest.mark.asyncio
    async def test_list_directory_tool(self, temp_workspace):
        (Path(temp_workspace) / "file1.txt").touch()
        (Path(temp_workspace) / "file2.txt").touch()
        subdir = Path(temp_workspace) / "subdir"
        subdir.mkdir()
        (subdir / "file3.txt").touch()
        
        from closeclaw.tools.file_tools import list_files_impl
        
        # Non-recursive
        files = await list_files_impl(str(temp_workspace), recursive=False)
        assert len(files) == 2
        
        # Recursive
        all_files = await list_files_impl(str(temp_workspace), recursive=True)
        assert len(all_files) == 3

    @pytest.mark.asyncio
    async def test_file_exists_tool(self, temp_workspace):
        # exists.txt was touched in list test if temp_workspace is shared, but let's be safe
        test_file = Path(temp_workspace) / "exists.txt"
        test_file.touch()
        
        from closeclaw.tools.file_tools import file_exists_impl
        assert await file_exists_impl(str(test_file)) is True
        assert await file_exists_impl(str(Path(temp_workspace) / "nope.txt")) is False
    
    @pytest.mark.asyncio
    async def test_get_file_size(self, temp_workspace):
        test_file = Path(temp_workspace) / "size.txt"
        content = "12345"
        test_file.write_text(content)
        
        from closeclaw.tools.file_tools import get_file_size_impl
        size = await get_file_size_impl(str(test_file))
        assert size == 5


class TestShellTools:
    """Test shell operation tools."""
    
    @pytest.mark.asyncio
    async def test_shell_pwd(self):
        from closeclaw.tools.shell_tools import shell_impl
        
        # Use a simple cross-platform command
        cmd = "echo hello" if os.name == "nt" else "pwd"
        result = await shell_impl(cmd)
        
        assert result.get("executed") is True
        assert result.get("returncode") == 0
        assert "hello" in result.get("stdout", "") or result.get("stdout") != ""
        
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "shell"), None)
        assert tool is not None
        assert tool.zone == Zone.ZONE_C
    
    @pytest.mark.asyncio
    async def test_shell_echo(self):
        from closeclaw.tools.shell_tools import shell_impl
        result = await shell_impl("echo test_output")
        assert result.get("executed") is True
        assert "test_output" in result.get("stdout", "")

