"""Tests for types system."""

import pytest
from datetime import datetime

from closeclaw.types import (
    Zone, AgentState, ToolType, OperationType, ChannelType,
    Tool, Session, Agent, AgentConfig,
    Message, ToolCall, ToolResult,
    AuthorizationRequest, AuthorizationResponse
)


class TestEnums:
    """Test enumeration definitions."""
    
    def test_zone_enum_values(self):
        """Test Zone enum has expected values."""
        assert Zone.ZONE_A.value == "A"
        assert Zone.ZONE_B.value == "B"
        assert Zone.ZONE_C.value == "C"
    
    def test_agent_state_enum_values(self):
        """Test AgentState enum has expected values."""
        assert AgentState.IDLE.value == "idle"
        assert AgentState.RUNNING.value == "running"
        assert AgentState.WAITING_FOR_AUTH.value == "waiting_for_auth"
        assert AgentState.ERROR.value == "error"
    
    def test_tool_type_enum_values(self):
        """Test ToolType enum has expected values."""
        assert ToolType.FILE.value == "file"
        assert ToolType.SHELL.value == "shell"
        assert ToolType.WEBSEARCH.value == "websearch"
    
    def test_operation_type_enum_values(self):
        """Test OperationType enum has expected values."""
        assert OperationType.READ.value == "read"
        assert OperationType.WRITE.value == "write"
        assert OperationType.DELETE.value == "delete"
    
    def test_channel_type_enum_values(self):
        """Test ChannelType enum has expected values."""
        assert ChannelType.TELEGRAM.value == "telegram"
        assert ChannelType.FEISHU.value == "feishu"
        assert ChannelType.CLI.value == "cli"


class TestTool:
    """Test Tool model."""
    
    def test_tool_creation(self, sample_tool_file):
        """Test basic tool creation."""
        assert sample_tool_file.name == "read_file"
        assert sample_tool_file.description == "Read file contents"
        assert sample_tool_file.zone == Zone.ZONE_A
        assert sample_tool_file.type == ToolType.FILE
    
    def test_tool_to_dict(self, sample_tool_file):
        """Test tool to_dict conversion."""
        tool_dict = sample_tool_file.to_dict()
        
        assert tool_dict["name"] == "read_file"
        assert tool_dict["zone"] == "A"
        assert tool_dict["type"] == "file"
        assert "parameters" in tool_dict
        assert "metadata" in tool_dict
    
    def test_tool_with_parameters(self):
        """Test tool with complex parameters."""
        tool = Tool(
            name="complex_tool",
            description="Tool with parameters",
            zone=Zone.ZONE_B,
            type=ToolType.FILE,
            parameters={
                "path": {"type": "string", "required": True},
                "mode": {"type": "string", "enum": ["read", "write"]},
                "encoding": {"type": "string", "default": "utf-8"},
            }
        )
        
        assert len(tool.parameters) == 3
        assert tool.parameters["path"]["required"] is True
        assert tool.parameters["encoding"]["default"] == "utf-8"
    
    def test_tool_with_metadata(self):
        """Test tool with metadata."""
        tool = Tool(
            name="test_tool",
            description="Test",
            zone=Zone.ZONE_A,
            type=ToolType.FILE,
            metadata={
                "timeout": 30,
                "retry_count": 3,
                "tags": ["important", "safe"]
            }
        )
        
        assert tool.metadata["timeout"] == 30
        assert "important" in tool.metadata["tags"]


class TestSession:
    """Test Session model."""
    
    def test_session_creation(self, sample_session):
        """Test basic session creation."""
        assert sample_session.session_id == "test_session_123"
        assert sample_session.user_id == "user_456"
        assert sample_session.channel_type == "cli"
    
    def test_session_to_dict(self, sample_session):
        """Test session to_dict conversion."""
        session_dict = sample_session.to_dict()
        
        assert session_dict["session_id"] == "test_session_123"
        assert session_dict["user_id"] == "user_456"
        assert "created_at" in session_dict
        assert isinstance(session_dict["created_at"], str)
    
    def test_session_timestamps(self, sample_session):
        """Test session has proper timestamps."""
        assert sample_session.created_at is not None
        assert sample_session.last_activity is not None
        assert isinstance(sample_session.created_at, datetime)
    
    def test_session_with_state(self, sample_session):
        """Test session with state data."""
        sample_session.state["current_operation"] = "reading_file"
        sample_session.state["operation_start"] = "2026-03-15T10:00:00"
        
        assert sample_session.state["current_operation"] == "reading_file"
        assert len(sample_session.state) == 2


class TestAgentConfig:
    """Test AgentConfig model."""
    
    def test_agent_config_creation(self, sample_agent_config):
        """Test basic agent config creation."""
        assert sample_agent_config.model == "openai/gpt-4"
        assert sample_agent_config.max_iterations == 10
        assert sample_agent_config.timeout_seconds == 300
        assert sample_agent_config.temperature == 0.0
    
    def test_agent_config_to_dict(self, sample_agent_config):
        """Test agent config to_dict conversion."""
        config_dict = sample_agent_config.to_dict()
        
        assert config_dict["model"] == "openai/gpt-4"
        assert config_dict["max_iterations"] == 10
        assert "temperature" in config_dict
    
    def test_agent_config_defaults(self):
        """Test agent config with default values."""
        config = AgentConfig(model="anthropic/claude-3")
        
        assert config.max_iterations == 10
        assert config.timeout_seconds == 300
        assert config.temperature == 0.0
        assert config.system_prompt is None


class TestAgent:
    """Test Agent model."""
    
    def test_agent_creation(self, sample_agent):
        """Test basic agent creation."""
        assert sample_agent.agent_id == "agent_001"
        assert sample_agent.state == AgentState.IDLE
        assert len(sample_agent.tools) == 2
    
    def test_agent_to_dict(self, sample_agent):
        """Test agent to_dict conversion."""
        agent_dict = sample_agent.to_dict()
        
        assert agent_dict["agent_id"] == "agent_001"
        assert agent_dict["state"] == "idle"
        assert "created_at" in agent_dict
    
    def test_agent_state_transitions(self, sample_agent):
        """Test agent state transitions."""
        assert sample_agent.state == AgentState.IDLE
        
        sample_agent.state = AgentState.RUNNING
        assert sample_agent.state == AgentState.RUNNING
        
        sample_agent.state = AgentState.WAITING_FOR_AUTH
        assert sample_agent.state == AgentState.WAITING_FOR_AUTH


class TestMessage:
    """Test Message model."""
    
    def test_message_creation(self, sample_message):
        """Test basic message creation."""
        assert sample_message.sender_name == "User"
        assert "read the file" in sample_message.content.lower()
        assert sample_message.timestamp is not None
    
    def test_message_assistant_role(self):
        """Test message with assistant role."""
        msg = Message(
            id="msg_002",
            channel_type="cli",
            sender_id="assistant_001",
            sender_name="Assistant",
            content="I'll read the file for you.",
            timestamp=datetime.utcnow()
        )
        
        assert msg.sender_name == "Assistant"
    
    def test_message_with_metadata(self):
        """Test message with metadata."""
        msg = Message(
            id="msg_003",
            channel_type="cli",
            sender_id="user_001",
            sender_name="User",
            content="Test message",
            timestamp=datetime.utcnow(),
            metadata={"source": "cli", "priority": "high"}
        )
        
        assert msg.metadata["source"] == "cli"


class TestToolCall:
    """Test ToolCall model."""
    
    def test_tool_call_creation(self, sample_tool_call):
        """Test basic tool call creation."""
        assert sample_tool_call.tool_id == "tool_1"
        assert sample_tool_call.name == "read_file"
        assert sample_tool_call.arguments["path"] == "/data/test.txt"
    
    def test_tool_call_with_complex_args(self):
        """Test tool call with complex arguments."""
        call = ToolCall(
            tool_id="tool_2",
            name="delete_file",
            arguments={
                "path": "/data/old.txt",
                "force": True,
                "recursive": True,
                "options": ["keep_backup", "log_action"]
            }
        )
        
        assert call.arguments["force"] is True
        assert len(call.arguments["options"]) == 2


class TestToolResult:
    """Test ToolResult model."""
    
    def test_tool_result_creation(self, sample_tool_result):
        """Test basic tool result creation."""
        assert sample_tool_result.tool_call_id == "tool_1"
        assert sample_tool_result.status == "success"
        assert "File contents" in sample_tool_result.result
    
    def test_tool_result_failure(self):
        """Test tool result with failure."""
        result = ToolResult(
            tool_call_id="tool_3",
            status="error",
            result="File not found: /data/nonexistent.txt",
            error="File not found",
        )
        
        assert result.status == "error"
        assert "not found" in result.result.lower()
    
    def test_tool_result_with_metadata(self):
        """Test tool result with metadata."""
        result = ToolResult(
            tool_call_id="tool_4",
            status="success",
            result="Search results...",
            metadata={"result_count": 42, "search_time_ms": 156}
        )
        
        assert result.metadata["result_count"] == 42


class TestAuthorizationModels:
    """Test Authorization request/response models."""
    
    def test_auth_request_creation(self):
        """Test creating authorization request."""
        auth_req = AuthorizationRequest(
            id="auth_1",
            operation_type="file_delete",
            tool_name="delete_file",
            description="Delete important file: /data/important.txt",
            created_at=datetime.utcnow()
        )
        
        assert auth_req.id == "auth_1"
        assert auth_req.tool_name == "delete_file"
        assert auth_req.operation_type == "file_delete"
    
    def test_auth_response_creation(self):
        """Test creating authorization response."""
        auth_resp = AuthorizationResponse(
            auth_request_id="auth_1",
            user_id="admin_001",
            approved=True,
            timestamp=datetime.utcnow(),
            comment="Verified user identity"
        )
        
        assert auth_resp.auth_request_id == "auth_1"
        assert auth_resp.approved is True
    
    def test_auth_response_rejection(self):
        """Test rejected authorization."""
        auth_resp = AuthorizationResponse(
            auth_request_id="auth_2",
            user_id="admin_001",
            approved=False,
            timestamp=datetime.utcnow(),
            comment="Suspicious operation, access denied"
        )
        
        assert auth_resp.approved is False
        assert "denied" in auth_resp.comment.lower()
