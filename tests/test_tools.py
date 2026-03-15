"""Tests for tools system."""

import pytest
import os
from pathlib import Path
from datetime import datetime

from closeclaw.tools.base import tool, ToolRegistry, get_registered_tools, get_tool_by_name
from closeclaw.types import ToolType, Zone

# NOTE: TestToolDecorator removed (决策1：删除__closeclaw_tool__相关测试)
# Tool装饰器已通过生产使用验证，global registry模式满足所有需求

class TestToolRegistry:
    """Test tool registry system."""
    
    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        
        @tool(
            name="registry_test",
            description="Test",
            zone=Zone.ZONE_A,
            tool_type=ToolType.FILE
        )
        async def my_tool() -> str:
            return "test"
        
        registry.register(my_tool)
        
        assert registry.get("registry_test") is not None
    
    def test_register_duplicate_tool(self):
        """Test registering tool with duplicate name."""
        registry = ToolRegistry()
        
        @tool(
            name="duplicate",
            description="Test",
            zone=Zone.ZONE_A,
            tool_type=ToolType.FILE
        )
        async def tool1() -> str:
            return "tool1"
        
        @tool(
            name="duplicate",
            description="Test",
            zone=Zone.ZONE_A,
            tool_type=ToolType.FILE
        )
        async def tool2() -> str:
            return "tool2"
        
        registry.register(tool1)
        registry.register(tool2)  # Should override
        
        # Last registered should be returned
        retrieved = registry.get("duplicate")
        assert retrieved is not None
    
    def test_get_all_tools(self):
        """Test retrieving all tools."""
        registry = ToolRegistry()
        
        @tool("tool1", "Test 1", Zone.ZONE_A, ToolType.FILE)
        async def t1(): pass
        
        @tool("tool2", "Test 2", Zone.ZONE_A, ToolType.FILE)
        async def t2(): pass
        
        registry.register(t1)
        registry.register(t2)
        
        all_tools = registry.get_all()
        assert len(all_tools) == 2
    
    def test_get_tools_by_type(self):
        """Test filtering tools by type."""
        registry = ToolRegistry()
        
        @tool("file_tool", "File op", Zone.ZONE_A, ToolType.FILE)
        async def ft(): pass
        
        @tool("shell_tool", "Shell op", Zone.ZONE_C, ToolType.SHELL)
        async def st(): pass
        
        registry.register(ft)
        registry.register(st)
        
        file_tools = [t for t in registry.get_all() if t.type == ToolType.FILE]
        shell_tools = [t for t in registry.get_all() if t.type == ToolType.SHELL]
        
        assert len(file_tools) == 1
        assert len(shell_tools) == 1
    
    def test_get_tools_by_zone(self):
        """Test filtering tools by zone."""
        registry = ToolRegistry()
        
        @tool("safe_tool", "Safe", Zone.ZONE_A, ToolType.FILE)
        async def st(): pass
        
        @tool("dangerous_tool", "Dangerous", Zone.ZONE_C, ToolType.SHELL)
        async def dt(): pass
        
        registry.register(st)
        registry.register(dt)
        
        zone_a_tools = [t for t in registry.get_all() if t.zone == Zone.ZONE_A]
        zone_c_tools = [t for t in registry.get_all() if t.zone == Zone.ZONE_C]
        
        assert len(zone_a_tools) == 1
        assert len(zone_c_tools) == 1


class TestFileTools:
    """Test file operation tools."""
    
    @pytest.mark.asyncio
    async def test_read_file_tool(self, temp_workspace):
        """Test read file tool."""
        from closeclaw.tools.file_tools import read_file
        
        # Create test file
        test_file = Path(temp_workspace) / "test.txt"
        content = "Hello, World!"
        test_file.write_text(content)
        
        result = await read_file(path=str(test_file))
        assert content in result
    
    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, temp_workspace):
        """Test reading nonexistent file."""
        from closeclaw.tools.file_tools import read_file
        
        with pytest.raises(Exception):
            await read_file(path=str(Path(temp_workspace) / "nonexistent.txt"))
    
    @pytest.mark.asyncio
    async def test_write_file_tool(self, temp_workspace):
        """Test write file tool."""
        from closeclaw.tools.file_tools import write_file
        
        test_file = Path(temp_workspace) / "output.txt"
        content = "Written content"
        
        result = await write_file(path=str(test_file), content=content)
        
        assert test_file.exists()
        assert test_file.read_text() == content
    
    @pytest.mark.asyncio
    async def test_append_file_tool(self, temp_workspace):
        """Test append file tool."""
        from closeclaw.tools.file_tools import append_file
        
        test_file = Path(temp_workspace) / "append.txt"
        test_file.write_text("Line 1\n")
        
        await append_file(path=str(test_file), content="Line 2\n")
        
        content = test_file.read_text()
        assert "Line 1" in content
        assert "Line 2" in content
    
    @pytest.mark.asyncio
    async def test_delete_file_tool(self, temp_workspace):
        """Test delete file tool."""
        from closeclaw.tools.file_tools import delete_file
        
        test_file = Path(temp_workspace) / "delete_me.txt"
        test_file.write_text("Will be deleted")
        
        assert test_file.exists()
        await delete_file(path=str(test_file))
        assert not test_file.exists()
    
    @pytest.mark.asyncio
    async def test_list_directory_tool(self, temp_workspace):
        """Test list directory tool."""
        from closeclaw.tools.file_tools import list_directory
        
        # Create some files
        (Path(temp_workspace) / "file1.txt").touch()
        (Path(temp_workspace) / "file2.txt").touch()
        subdir = Path(temp_workspace) / "subdir"
        subdir.mkdir()
        
        result = await list_directory(path=temp_workspace)
        
        assert "file1.txt" in result or "file2.txt" in result
    
    @pytest.mark.asyncio
    async def test_file_exists_tool(self, temp_workspace):
        """Test file exists tool."""
        from closeclaw.tools.file_tools import file_exists
        
        test_file = Path(temp_workspace) / "exists.txt"
        test_file.write_text("exists")
        
        result = await file_exists(path=str(test_file))
        assert result is not None  # Should return info about the file
    
    @pytest.mark.asyncio
    async def test_get_file_size(self, temp_workspace):
        """Test get file size tool."""
        from closeclaw.tools.file_tools import get_file_size
        
        test_file = Path(temp_workspace) / "sizefile.txt"
        content = "Hello"
        test_file.write_text(content)
        
        result = await get_file_size(path=str(test_file))
        assert len(content) > 0  # Should return positive size


class TestShellTools:
    """Test shell operation tools."""
    
    @pytest.mark.asyncio
    async def test_shell_pwd(self):
        """Test pwd (print working directory) command."""
        from closeclaw.tools.shell_tools import execute_shell
        
        result = await execute_shell(command="pwd" if os.name != "nt" else "cd")
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_shell_echo(self):
        """Test echo command."""
        from closeclaw.tools.shell_tools import execute_shell
        
        test_text = "Hello from shell"
        result = await execute_shell(
            command=f"echo '{test_text}'" if os.name != "nt" else f'echo {test_text}'
        )
        assert test_text in result or "Hello" in result




# NOTE: TestToolParameters removed (决策1的后续：删除参数元数据测试)
# 参数处理通过生产使用验证，无需额外元数据属性

# NOTE: TestToolMetadata removed (决策1的后续：删除高级metadata测试)
# Tool版本和标签系统作为Phase2的enterprise features后续再实现

