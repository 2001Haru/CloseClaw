"""Tests for ToolAdaptationLayer (Phase 2)."""

import pytest
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from closeclaw.tools import ToolAdaptationLayer, ExecutionMode, ToolMetadata
from closeclaw.types import Tool, ToolType,  ToolCall, ToolResult
from closeclaw.agents.task_manager import TaskManager

logger = logging.getLogger(__name__)


class TestToolAdaptationLayer:
    """Test cases for ToolAdaptationLayer routing logic."""
    
    @pytest.fixture
    def adapter(self):
        """Create a ToolAdaptationLayer instance."""
        return ToolAdaptationLayer()
    
    @pytest.fixture
    def sample_tools(self):
        """Create sample tools for testing."""
        async def fast_tool():
            return {"result": "fast"}
        
        async def slow_tool():
            return {"result": "slow"}
        
        fast = Tool(
            name="fast_op",
            description="Fast operation",
            type=ToolType.FILE,
            need_auth=False,
            handler=fast_tool,
        )
        
        slow = Tool(
            name="slow_op",
            description="Slow operation (web search)",
            type=ToolType.WEBSEARCH,
            need_auth=False,
            handler=slow_tool,
        )
        
        return {"fast": fast, "slow": slow}
    
    def test_tool_metadata_creation(self, adapter, sample_tools):
        """Test: Register tool with metadata."""
        tool = sample_tools["fast"]
        
        adapter.register_tool_metadata(
            tool,
            estimated_duration_seconds=0.5,
            execution_mode=ExecutionMode.SYNC,
        )
        
        metadata = adapter.get_tool_metadata("fast_op")
        assert metadata is not None
        assert metadata.tool.name == "fast_op"
        assert metadata.estimated_duration_seconds == 0.5
        assert metadata.execution_mode == ExecutionMode.SYNC
    
    def test_should_use_background_task_explicit_mode(self, adapter, sample_tools):
        """Test: Explicit execution mode determines routing."""
        tool = sample_tools["fast"]
        
        # Force async mode even for fast tool
        adapter.register_tool_metadata(
            tool,
            estimated_duration_seconds=0.5,
            execution_mode=ExecutionMode.ASYNC_BG,
        )
        
        metadata = adapter.get_tool_metadata("fast_op")
        assert metadata.should_use_background_task() is True
    
    def test_should_use_background_task_auto_slow(self, adapter, sample_tools):
        """Test: Auto-decide background for slow tools."""
        tool = sample_tools["slow"]
        
        # Register with long duration (should auto-select ASYNC_BG)
        adapter.register_tool_metadata(
            tool,
            estimated_duration_seconds=5.0,  # > 2 seconds threshold
            # execution_mode not specified - should auto-decide
        )
        
        metadata = adapter.get_tool_metadata("slow_op")
        assert metadata.execution_mode == ExecutionMode.ASYNC_BG
        assert metadata.should_use_background_task() is True
    
    def test_should_use_background_task_auto_fast(self, adapter, sample_tools):
        """Test: Auto-decide sync for fast tools."""
        tool = sample_tools["fast"]
        
        # Register with short duration
        adapter.register_tool_metadata(
            tool,
            estimated_duration_seconds=0.5,  # < 2 seconds threshold
        )
        
        metadata = adapter.get_tool_metadata("fast_op")
        assert metadata.execution_mode == ExecutionMode.SYNC
        assert metadata.should_use_background_task() is False
    
    def test_auto_classify_websearch_tool(self, adapter, sample_tools):
        """Test: WEBSEARCH tools auto-classified as async."""
        tool = sample_tools["slow"]
        
        # Register without metadata - should auto-classify
        adapter.register_tool_metadata(tool)
        
        metadata = adapter.get_tool_metadata("slow_op")
        assert metadata.execution_mode == ExecutionMode.ASYNC_BG
        logger.info("WEBSEARCH tool auto-classified as async")
    
    @pytest.mark.asyncio
    async def test_execute_tool_call_sync_mode(self, adapter, sample_tools):
        """Test: Sync tool call executes directly."""
        tool = sample_tools["fast"]
        adapter.register_tool_metadata(
            tool,
            estimated_duration_seconds=0.5,
            execution_mode=ExecutionMode.SYNC,
        )
        
        # Create tool call
        tool_call = ToolCall(
            tool_id="tc_001",
            name="fast_op",
            arguments={},
        )
        
        # Create mock direct executor
        async def direct_executor(tc):
            return ToolResult(
                tool_call_id=tc.tool_id,
                status="success",
                result={"result": "fast_executed"},
            )
        
        # Execute
        result = await adapter.execute_tool_call(
            tool_call=tool_call,
            available_tools={"fast_op": tool},
            direct_executor=direct_executor,
        )
        
        # Verify
        assert result.status == "success"
        assert result.result == {"result": "fast_executed"}
        assert result.metadata.get("routing") == "direct"
    
    @pytest.mark.asyncio
    async def test_execute_tool_call_async_mode(self, adapter, sample_tools):
        """Test: Async tool call creates background task."""
        tool = sample_tools["slow"]
        adapter.register_tool_metadata(
            tool,
            estimated_duration_seconds=5.0,
            execution_mode=ExecutionMode.ASYNC_BG,
        )
        
        # Create mock task manager
        task_manager = AsyncMock()
        task_manager.create_task.return_value = "#001"
        
        # Create tool call
        tool_call = ToolCall(
            tool_id="tc_001",
            name="slow_op",
            arguments={"param": "value"},
        )
        
        # Execute
        result = await adapter.execute_tool_call(
            tool_call=tool_call,
            available_tools={"slow_op": tool},
            task_manager=task_manager,
        )
        
        # Verify
        assert result.status == "task_created"
        assert result.result["task_id"] == "#001"
        assert result.metadata.get("routing") == "background"
        
        # Verify task manager was called
        task_manager.create_task.assert_called_once_with(
            tool_name="slow_op",
            arguments={"param": "value"},
        )
    
    @pytest.mark.asyncio
    async def test_execute_tool_call_not_found(self, adapter):
        """Test: Tool not found returns error."""
        tool_call = ToolCall(
            tool_id="tc_001",
            name="nonexistent",
            arguments={},
        )
        
        result = await adapter.execute_tool_call(
            tool_call=tool_call,
            available_tools={},  # Empty
            task_manager=None,
        )
        
        assert result.status == "error"
        assert "not found" in result.error.lower()
    
    def test_list_tools_with_metadata(self, adapter, sample_tools):
        """Test: List all tools with their metadata."""
        fast_tool = sample_tools["fast"]
        slow_tool = sample_tools["slow"]
        
        adapter.register_tool_metadata(fast_tool, estimated_duration_seconds=0.5)
        adapter.register_tool_metadata(slow_tool, estimated_duration_seconds=5.0)
        
        tools_list = adapter.list_tools_with_metadata()
        
        assert len(tools_list) == 2
        
        # Verify fast tool
        fast_entry = next(t for t in tools_list if t["name"] == "fast_op")
        assert fast_entry["mode"] == "sync"
        assert fast_entry["estimated_seconds"] == 0.5
        
        # Verify slow tool
        slow_entry = next(t for t in tools_list if t["name"] == "slow_op")
        assert slow_entry["mode"] == "async_bg"
        assert slow_entry["estimated_seconds"] == 5.0


class TestToolAdaptationIntegration:
    """Integration tests with Agent core."""
    
    @pytest.mark.asyncio
    async def test_adapter_with_agent_core(self):
        """Test: ToolAdaptationLayer works with AgentCore."""
        from closeclaw.agents import AgentCore
        from closeclaw.types import AgentConfig
        from unittest.mock import AsyncMock
        
        # Create agent
        mock_llm = AsyncMock()
        agent = AgentCore(
            agent_id="test_agent",
            llm_provider=mock_llm,
            config=AgentConfig(model="gpt-4"),
            workspace_root="/tmp",
            admin_user_id="admin",
        )
        
        # Verify adapter is initialized
        assert hasattr(agent, 'tool_adaptation_layer')
        assert isinstance(agent.tool_adaptation_layer, ToolAdaptationLayer)
        
        # Register tool
        async def test_handler():
            return {"ok": True}
        
        tool = Tool(
            name="test_tool",
            description="Test",
            type=ToolType.FILE,
            need_auth=False,
            handler=test_handler,
        )
        
        agent.register_tool(tool)
        
        # Verify tool is in both places
        assert "test_tool" in agent.tools
        assert agent.tool_adaptation_layer.get_tool_metadata("test_tool") is not None


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    pytest.main([__file__, "-v"])




