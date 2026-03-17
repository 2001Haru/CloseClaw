"""Integration tests for memory retrieval in AgentCore."""

import pytest
import os
import shutil
from datetime import datetime
from unittest.mock import patch, MagicMock
import numpy as np

from closeclaw.agents.core import AgentCore
from closeclaw.types import (
    AgentConfig, Session, Message, ToolCall, ToolResult, Zone, ToolType
)

@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace for testing."""
    workspace = tmp_path / "test_memory_integration"
    workspace.mkdir()
    yield str(workspace)
    if os.path.exists(workspace):
        shutil.rmtree(workspace)

@pytest.fixture
def mock_llm():
    """Mock LLM provider."""
    mock = MagicMock()
    return mock

@pytest.fixture
def agent(temp_workspace, mock_llm, sample_agent_config):
    """Create an AgentCore instance with mocked embedding."""
    with patch("closeclaw.memory.memory_manager.TextEmbedding") as MockEmbedding:
        # Mock the embed method to return random vectors
        mock_instance = MockEmbedding.return_value
        
        def mock_embed(texts):
            for _ in texts:
                yield np.random.rand(384).astype(np.float32)
        
        mock_instance.embed.side_effect = mock_embed
        
        agent = AgentCore(
            agent_id="test_agent",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        # Force the mock to be used in memory_manager
        agent.memory_manager._embedding_model = mock_instance
        
        return agent

@pytest.mark.asyncio
async def test_retrieve_memory_tool_execution(agent, temp_workspace):
    """Test that AgentCore can execute the retrieve_memory tool."""
    # 1. Add some test memories directly to the manager
    agent.memory_manager.add_memory(
        content="The project code name is 'CloseClaw'.",
        source="file:project_info.md",
        session_id="session_123"
    )
    
    # 2. Start a session
    session = await agent.start_session("session_123", "user_456", "cli")
    
    # 3. Simulate a tool call for retrieve_memory
    tool_call = ToolCall(
        tool_id="tc_mem_1",
        name="retrieve_memory",
        arguments={"query": "project code name"}
    )
    
    # 4. Process the tool call
    result = await agent._process_tool_call(tool_call)
    
    # 5. Verify results
    assert result.status == "success"
    assert "CloseClaw" in result.result
    assert "Score:" in result.result

@pytest.mark.asyncio
async def test_retrieve_memory_no_results(agent):
    """Test retrieve_memory when no results are found."""
    await agent.start_session("session_123", "user_456", "cli")
    
    tool_call = ToolCall(
        tool_id="tc_mem_2",
        name="retrieve_memory",
        arguments={"query": "non-existent topic"}
    )
    
    result = await agent._process_tool_call(tool_call)
    
    assert result.status == "success"
    assert "No relevant memories found" in result.result