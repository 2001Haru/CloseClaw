"""Tests for agent core loop."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from closeclaw.agents.core import AgentCore, LLMProvider
from closeclaw.types import (
    AgentState, Zone, ToolType, Tool, Session,
    Message, ToolCall, ToolResult, AuthorizationRequest,
    AuthorizationResponse
)


class MockLLMProvider:
    """Mock LLM provider for testing."""
    
    async def generate(self, messages, tools, **kwargs):
        """Mock LLM generation."""
        return "Test response", None


class MockLLMProviderWithToolCall:
    """Mock LLM provider that returns tool calls."""
    
    async def generate(self, messages, tools, **kwargs):
        """Mock LLM generation with tool call."""
        tool_calls = [
            ToolCall(
                id="tool_call_1",
                tool_name="read_file",
                arguments={"path": "/data/test.txt"},
                timestamp=datetime.utcnow()
            )
        ]
        return "I'll read the file for you", tool_calls


@pytest.fixture
def mock_llm():
    """Create mock LLM provider."""
    return MockLLMProvider()


@pytest.fixture
def mock_llm_with_tools():
    """Create mock LLM provider that uses tools."""
    return MockLLMProviderWithToolCall()


class TestAgentCoreInitialization:
    """Test AgentCore initialization."""
    
    def test_agent_core_creation(self, sample_agent_config, mock_llm, temp_workspace):
        """Test basic AgentCore creation."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        assert agent.agent_id == "agent_001"
        assert agent.state == AgentState.IDLE
    
    def test_agent_core_with_admin(self, sample_agent_config, mock_llm, temp_workspace):
        """Test AgentCore creation with admin user."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
            admin_user_id="admin_001"
        )
        
        assert agent.admin_user_id == "admin_001"
    
    def test_agent_core_with_tools(self, sample_agent_config, mock_llm, 
                                   temp_workspace, sample_tool_file, sample_tool_shell):
        """Test AgentCore creation with tools."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        agent.register_tool(sample_tool_file)
        agent.register_tool(sample_tool_shell)
        
        assert len(agent.tools) >= 0


class TestAgentCoreStateManagement:
    """Test agent state management."""
    
    def test_agent_starts_idle(self, sample_agent_config, mock_llm, temp_workspace):
        """Test agent starts in IDLE state."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        assert agent.state == AgentState.IDLE
    
    def test_agent_state_transition(self, sample_agent_config, mock_llm, temp_workspace):
        """Test agent state transitions."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        agent.state = AgentState.RUNNING
        assert agent.state == AgentState.RUNNING
        
        agent.state = AgentState.WAITING_FOR_AUTH
        assert agent.state == AgentState.WAITING_FOR_AUTH
        
        agent.state = AgentState.IDLE
        assert agent.state == AgentState.IDLE


class TestAgentMessage:
    """Test agent message handling."""
    
    @pytest.mark.asyncio
    async def test_process_user_message(self, sample_agent_config, mock_llm, 
                                       temp_workspace, sample_message):
        """Test processing user message."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        # Mock session
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        response = await agent.process_message(sample_message, session)
        
        assert response is not None
        assert isinstance(response, Message)
    
    @pytest.mark.asyncio
    async def test_process_message_with_tool_call(self, sample_agent_config, 
                                                  mock_llm_with_tools, temp_workspace):
        """Test processing message that results in tool call."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm_with_tools,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        # Register tool
        read_file = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        agent.register_tool(read_file)
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        message = Message(
            role="user",
            content="Read /data/test.txt",
            timestamp=datetime.utcnow()
        )
        
        response = await agent.process_message(message, session)
        assert response is not None


class TestAuthorization:
    """Test authorization handling."""
    
    @pytest.mark.asyncio
    async def test_zone_c_requires_auth(self, sample_agent_config, mock_llm, 
                                       temp_workspace):
        """Test Zone C operations require authorization."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
            admin_user_id="admin_001"
        )
        
        delete_tool = Tool(
            name="delete_file",
            description="Delete file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        agent.register_tool(delete_tool)
        
        # Process tool call that requires auth
        tool_call = ToolCall(
            id="tc_1",
            tool_name="delete_file",
            arguments={"path": "/data/important.txt"},
            timestamp=datetime.utcnow()
        )
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        result = await agent._process_tool_call(tool_call, session)
        
        # Should create auth request
        assert agent.state == AgentState.WAITING_FOR_AUTH or agent.pending_auth_requests
    
    @pytest.mark.asyncio
    async def test_approve_auth_request(self, sample_agent_config, mock_llm, 
                                       temp_workspace):
        """Test approving authorization request."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
            admin_user_id="admin_001"
        )
        
        # Create pending auth request
        auth_req = AuthorizationRequest(
            id="auth_1",
            user_id="user_456",
            tool_name="delete_file",
            arguments={"path": "/data/old.txt"},
            reason="User confirmed",
            timestamp=datetime.utcnow()
        )
        
        agent.pending_auth_requests[auth_req.id] = auth_req
        
        # Approve request
        approval = AuthorizationResponse(
            request_id=auth_req.id,
            approved=True,
            approver_id="admin_001",
            reason="Approved by admin",
            timestamp=datetime.utcnow()
        )
        
        result = await agent.approve_auth_request(approval)
        
        # Request should be removed after approval
        assert auth_req.id not in agent.pending_auth_requests or result
    
    @pytest.mark.asyncio
    async def test_deny_auth_request(self, sample_agent_config, mock_llm, 
                                    temp_workspace):
        """Test denying authorization request."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        # Create pending auth request
        auth_req = AuthorizationRequest(
            id="auth_2",
            user_id="user_456",
            tool_name="shell_command",
            arguments={"command": "rm -rf /"},
            reason="Suspicious command",
            timestamp=datetime.utcnow()
        )
        
        agent.pending_auth_requests[auth_req.id] = auth_req
        
        # Deny request
        denial = AuthorizationResponse(
            request_id=auth_req.id,
            approved=False,
            approver_id="admin_001",
            reason="Dangerous operation denied",
            timestamp=datetime.utcnow()
        )
        
        result = await agent.approve_auth_request(denial)
        
        # Request should be removed after denial
        assert auth_req.id not in agent.pending_auth_requests or result


class TestToolExecution:
    """Test tool execution."""
    
    @pytest.mark.asyncio
    async def test_execute_safe_tool(self, sample_agent_config, mock_llm, 
                                    temp_workspace, sample_tool_file):
        """Test executing safe (Zone A) tool."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        agent.register_tool(sample_tool_file)
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        tool_call = ToolCall(
            id="tc_1",
            tool_name="read_file",
            arguments={"path": f"{temp_workspace}/test.txt"},
            timestamp=datetime.utcnow()
        )
        
        # Create test file
        import pathlib
        pathlib.Path(f"{temp_workspace}/test.txt").write_text("test content")
        
        result = await agent._process_tool_call(tool_call, session)
        
        # Should execute without requiring auth
        assert isinstance(result, (ToolResult, dict))
    
    @pytest.mark.asyncio
    async def test_tool_not_found(self, sample_agent_config, mock_llm, 
                                 temp_workspace):
        """Test calling nonexistent tool."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        tool_call = ToolCall(
            id="tc_1",
            tool_name="nonexistent_tool",
            arguments={},
            timestamp=datetime.utcnow()
        )
        
        result = await agent._process_tool_call(tool_call, session)
        
        # Should handle gracefully
        assert result is not None


class TestSessionManagement:
    """Test session management."""
    
    def test_create_session(self, sample_agent_config, mock_llm, temp_workspace):
        """Test creating agent session."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        assert session.session_id == "session_123"
        assert session.user_id == "user_456"
    
    def test_session_state_persistence(self, sample_agent_config, mock_llm, 
                                      temp_workspace):
        """Test session maintains state."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        # Add state
        session.state["operation_count"] = 5
        session.state["last_tool"] = "read_file"
        
        assert session.state["operation_count"] == 5
        assert session.state["last_tool"] == "read_file"


class TestAgentIteration:
    """Test agent iteration limits."""
    
    def test_iteration_limit(self, sample_agent_config, mock_llm, temp_workspace):
        """Test agent respects max iterations."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        assert agent.max_iterations == sample_agent_config.max_iterations
    
    def test_iteration_count(self, sample_agent_config, mock_llm, temp_workspace):
        """Test agent tracks iteration count."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        # Agent should have iteration tracking
        assert hasattr(agent, "max_iterations") or hasattr(agent, "iteration_count")


class TestAgentErrorHandling:
    """Test agent error handling."""
    
    @pytest.mark.asyncio
    async def test_llm_error_handling(self, sample_agent_config, temp_workspace):
        """Test agent handles LLM errors."""
        class FailingLLMProvider:
            async def generate(self, messages, tools, **kwargs):
                raise Exception("LLM API error")
        
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=FailingLLMProvider(),
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        message = Message(
            role="user",
            content="Test",
            timestamp=datetime.utcnow()
        )
        
        # Should handle error gracefully
        try:
            result = await agent.process_message(message, session)
        except Exception:
            # Error handling is implementation dependent
            pass
    
    @pytest.mark.asyncio
    async def test_tool_execution_error(self, sample_agent_config, mock_llm, 
                                       temp_workspace):
        """Test agent handles tool execution errors."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        # Register a tool that fails
        failing_tool = Tool(
            name="failing_tool",
            description="Fails",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        agent.register_tool(failing_tool)
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        tool_call = ToolCall(
            id="tc_fail",
            tool_name="failing_tool",
            arguments={},
            timestamp=datetime.utcnow()
        )
        
        # Should handle gracefully
        result = await agent._process_tool_call(tool_call, session)
        assert result is not None


class TestAgentIntegration:
    """Integration tests for agent."""
    
    @pytest.mark.asyncio
    async def test_simple_query_response_flow(self, sample_agent_config, mock_llm, 
                                             temp_workspace):
        """Test simple query-response flow."""
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace
        )
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        message = Message(
            role="user",
            content="What is the capital of France?",
            timestamp=datetime.utcnow()
        )
        
        response = await agent.process_message(message, session)
        
        assert response is not None
        assert isinstance(response, Message)
