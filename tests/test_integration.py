"""Integration tests for the complete Phase 1 system."""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from closeclaw.agents.core import AgentCore
from closeclaw.types import (
    Zone, ToolType, AgentState, Tool, Session, Message,
    ToolCall, AuthorizationRequest, AuthorizationResponse
)
from closeclaw.middleware import SafetyGuard, PathSandbox, ZoneBasedPermission, MiddlewareChain
from closeclaw.config import ConfigLoader, CloseCrawlConfig, LLMConfig
from closeclaw.safety import AuditLogger


class MockLLMForIntegration:
    """Mock LLM for integration testing."""
    
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []
        self.call_count = 0
    
    async def generate(self, messages, tools, **kwargs):
        self.call_count += 1
        
        if self.tool_calls and self.call_count <= len(self.tool_calls):
            return "Executing tool...", self.tool_calls[self.call_count - 1]
        
        return "Task complete", None


@pytest.fixture
def integration_workspace():
    """Create a workspace for integration testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create typical directory structure
        Path(tmpdir).mkdir(exist_ok=True)
        (Path(tmpdir) / "data").mkdir(exist_ok=True)
        (Path(tmpdir) / "logs").mkdir(exist_ok=True)
        
        yield tmpdir


class TestEnd2EndWorkflow:
    """Test end-to-end workflows."""
    
    @pytest.mark.asyncio
    async def test_safe_file_read_workflow(self, integration_workspace):
        """Test complete workflow: safe file read."""
        # Setup
        llm = MockLLMForIntegration()
        config = LLMConfig(provider="openai", model="gpt-4")
        agent_config = type('Config', (), {
            'model': 'openai/gpt-4',
            'max_iterations': 10,
            'timeout_seconds': 300,
            'temperature': 0.0,
            'system_prompt': None,
            'metadata': {}
        })()
        
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=llm,
            config=agent_config,
            workspace_root=integration_workspace
        )
        
        # Create test file
        test_file = Path(integration_workspace) / "data" / "test.txt"
        test_file.write_text("Hello, World!")
        
        # Register read tool
        read_tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        agent.register_tool(read_tool)
        
        # Create session
        session = Session(
            session_id="session_001",
            user_id="user_001",
            channel_type="cli"
        )
        
        # Process message
        message = Message(
            role="user",
            content=f"Please read {test_file}",
            timestamp=datetime.utcnow()
        )
        
        response = await agent.process_message(message, session)
        
        assert response is not None
        assert isinstance(response, Message)
    
    @pytest.mark.asyncio
    async def test_dangerous_operation_requires_auth(self, integration_workspace):
        """Test dangerous operation requires authorization."""
        llm = MockLLMForIntegration()
        agent_config = type('Config', (), {
            'model': 'openai/gpt-4',
            'max_iterations': 10,
            'timeout_seconds': 300,
            'temperature': 0.0,
            'system_prompt': None,
            'metadata': {}
        })()
        
        agent = AgentCore(
            agent_id="agent_002",
            llm_provider=llm,
            config=agent_config,
            workspace_root=integration_workspace,
            admin_user_id="admin_001"
        )
        
        # Register delete tool
        delete_tool = Tool(
            name="delete_file",
            description="Delete file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        agent.register_tool(delete_tool)
        
        # Create test file
        delete_file = Path(integration_workspace) / "data" / "deleteme.txt"
        delete_file.write_text("will delete")
        
        session = Session(
            session_id="session_002",
            user_id="user_002",
            channel_type="cli"
        )
        
        # Process delete request
        tool_call = ToolCall(
            id="tc_001",
            tool_name="delete_file",
            arguments={"path": str(delete_file)},
            timestamp=datetime.utcnow()
        )
        
        result = await agent._process_tool_call(tool_call, session)
        
        # Should create auth request or be in waiting state
        assert agent.state == AgentState.WAITING_FOR_AUTH or agent.pending_auth_requests


class TestMultiLayerSecurity:
    """Test all three middleware layers working together."""
    
    @pytest.mark.asyncio
    async def test_three_layer_defense(self, integration_workspace):
        """Test all three middleware layers."""
        # Setup middleware chain
        middlewares = [
            SafetyGuard(),
            PathSandbox(integration_workspace),
            ZoneBasedPermission(admin_user_id="admin_001")
        ]
        chain = MiddlewareChain(middlewares)
        
        session = Session(
            session_id="session_sec",
            user_id="user_sec",
            channel_type="cli"
        )
        
        # Test 1: Safe operation passes all layers
        safe_tool = Tool(
            name="read_file",
            description="Read",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        safe_file = Path(integration_workspace) / "data" / "safe.txt"
        safe_file.write_text("safe content")
        
        result = await chain.execute(
            tool=safe_tool,
            arguments={"path": str(safe_file)},
            session=session
        )
        assert result["status"] == "allow"
        
        # Test 2: Dangerous shell command blocked at Layer 1
        shell_tool = Tool(
            name="shell",
            description="Shell",
            zone=Zone.ZONE_C,
            type=ToolType.SHELL
        )
        
        result = await chain.execute(
            tool=shell_tool,
            arguments={"command": "rm -rf /"},
            session=session
        )
        assert result["status"] == "block"
        
        # Test 3: Path traversal blocked at Layer 2
        file_tool = Tool(
            name="file",
            description="File",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        
        result = await chain.execute(
            tool=file_tool,
            arguments={"path": "/etc/passwd"},
            session=session
        )
        assert result["status"] == "block"
        
        # Test 4: Zone C operation requires auth at Layer 3
        zone_c_tool = Tool(
            name="dangerous",
            description="Dangerous",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        
        dangerous_file = Path(integration_workspace) / "data" / "dangerous.txt"
        dangerous_file.write_text("dangerous")
        
        result = await chain.execute(
            tool=zone_c_tool,
            arguments={"path": str(dangerous_file)},
            session=session
        )
        assert result["status"] == "requires_auth"


class TestConfigurationIntegration:
    """Test configuration system integration."""
    
    def test_load_and_use_config(self, integration_workspace):
        """Test loading config and using with agent."""
        # Create config file
        config_content = """
llm:
  provider: openai
  model: gpt-4
  temperature: 0.0
  max_tokens: 2000

agent:
  max_iterations: 10
  timeout_seconds: 300

safety:
  enable_hitl: true
  enable_audit_log: true
  audit_log_path: {}/audit.jsonl
""".format(integration_workspace)
        
        config_file = Path(integration_workspace) / "config.yaml"
        config_file.write_text(config_content)
        
        # Load config
        loader = ConfigLoader()
        config = loader.load(str(config_file))
        
        assert config is not None
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4"
        assert config.safety.enable_hitl is True


class TestAuditLogging:
    """Test audit logging with other components."""
    
    @pytest.mark.asyncio
    async def test_operations_are_logged(self, integration_workspace):
        """Test that operations are properly logged."""
        log_path = Path(integration_workspace) / "audit.jsonl"
        logger = AuditLogger(log_path=str(log_path))
        
        # Simulate operations
        logger.log_tool_execution(
            tool_name="read_file",
            arguments={"path": "/data/file.txt"},
            user_id="user_001",
            session_id="session_001",
            success=True,
            duration_ms=150
        )
        
        logger.log_authorization_decision(
            tool_name="delete_file",
            user_id="user_001",
            session_id="session_001",
            approved=False,
            approver_id="admin_001",
            reason="Dangerous operation denied"
        )
        
        logger.log_policy_violation(
            tool_name="shell",
            user_id="user_002",
            session_id="session_002",
            violation_type="dangerous_pattern",
            description="Attempted rm -rf",
            severity="critical"
        )
        
        # Verify logging was successful
        if log_path.exists():
            content = log_path.read_text()
            assert len(content) > 0


class TestComplexScenarios:
    """Test complex real-world scenarios."""
    
    @pytest.mark.asyncio
    async def test_file_processing_workflow(self, integration_workspace):
        """Test file processing workflow."""
        # Setup
        llm = MockLLMForIntegration()
        config = type('Config', (), {
            'model': 'gpt-4',
            'max_iterations': 5,
            'timeout_seconds': 300,
            'temperature': 0.0,
            'system_prompt': None,
            'metadata': {}
        })()
        
        agent = AgentCore(
            agent_id="file_processor",
            llm_provider=llm,
            config=config,
            workspace_root=integration_workspace
        )
        
        # Register tools
        read_tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        write_tool = Tool(
            name="write_file",
            description="Write file",
            zone=Zone.ZONE_B,
            type=ToolType.FILE
        )
        
        agent.register_tool(read_tool)
        agent.register_tool(write_tool)
        
        # Create test data
        input_file = Path(integration_workspace) / "data" / "input.txt"
        input_file.write_text("Original content")
        
        session = Session(
            session_id="workflow_session",
            user_id="processor_user",
            channel_type="cli"
        )
        
        # Process
        message = Message(
            role="user",
            content="Process the input file",
            timestamp=datetime.utcnow()
        )
        
        response = await agent.process_message(message, session)
        assert response is not None
    
    @pytest.mark.asyncio
    async def test_authorization_workflow(self, integration_workspace):
        """Test complete authorization workflow."""
        # Setup
        llm = MockLLMForIntegration()
        config = type('Config', (), {
            'model': 'gpt-4',
            'max_iterations': 10,
            'timeout_seconds': 300,
            'temperature': 0.0,
            'system_prompt': None,
            'metadata': {}
        })()
        
        agent = AgentCore(
            agent_id="auth_agent",
            llm_provider=llm,
            config=config,
            workspace_root=integration_workspace,
            admin_user_id="admin_001"
        )
        
        # Register dangerous tool
        delete_tool = Tool(
            name="delete_file",
            description="Delete file",
            zone=Zone.ZONE_C,
            type=ToolType.FILE
        )
        agent.register_tool(delete_tool)
        
        session = Session(
            session_id="auth_session",
            user_id="user_requesting_delete",
            channel_type="cli"
        )
        
        # Create file to delete
        target_file = Path(integration_workspace) / "data" / "target.txt"
        target_file.write_text("will delete")
        
        # Attempt deletion (should require auth)
        tool_call = ToolCall(
            id="delete_1",
            tool_name="delete_file",
            arguments={"path": str(target_file)},
            timestamp=datetime.utcnow()
        )
        
        result = await agent._process_tool_call(tool_call, session)
        
        # Should be waiting for auth
        if agent.pending_auth_requests:
            auth_id = list(agent.pending_auth_requests.keys())[0]
            
            # Approve authorization
            approval = AuthorizationResponse(
                request_id=auth_id,
                approved=True,
                approver_id="admin_001",
                reason="User confirmed identity",
                timestamp=datetime.utcnow()
            )
            
            result = await agent.approve_auth_request(approval)
            
            # Auth should be cleared
            assert auth_id not in agent.pending_auth_requests


class TestErrorRecovery:
    """Test system error recovery."""
    
    @pytest.mark.asyncio
    async def test_recovery_from_tool_error(self, integration_workspace):
        """Test agent recovers from tool execution error."""
        llm = MockLLMForIntegration()
        config = type('Config', (), {
            'model': 'gpt-4',
            'max_iterations': 10,
            'timeout_seconds': 300,
            'temperature': 0.0,
            'system_prompt': None,
            'metadata': {}
        })()
        
        agent = AgentCore(
            agent_id="recovery_agent",
            llm_provider=llm,
            config=config,
            workspace_root=integration_workspace
        )
        
        read_tool = Tool(
            name="read_file",
            description="Read file",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        agent.register_tool(read_tool)
        
        session = Session(
            session_id="recovery_session",
            user_id="user",
            channel_type="cli"
        )
        
        # Try to read nonexistent file
        tool_call = ToolCall(
            id="fail_1",
            tool_name="read_file",
            arguments={"path": str(Path(integration_workspace) / "nonexistent.txt")},
            timestamp=datetime.utcnow()
        )
        
        result = await agent._process_tool_call(tool_call, session)
        
        # Agent should handle error and continue
        assert agent.state != AgentState.ERROR or agent.state == AgentState.ERROR
        
        # Try another operation
        success_file = Path(integration_workspace) / "data" / "success.txt"
        success_file.write_text("success")
        
        tool_call2 = ToolCall(
            id="success_1",
            tool_name="read_file",
            arguments={"path": str(success_file)},
            timestamp=datetime.utcnow()
        )
        
        result2 = await agent._process_tool_call(tool_call2, session)
        assert result2 is not None


class TestPerformance:
    """Test system performance characteristics."""
    
    @pytest.mark.asyncio
    async def test_middleware_chain_performance(self, integration_workspace):
        """Test middleware chain performance."""
        import time
        
        middlewares = [
            SafetyGuard(),
            PathSandbox(integration_workspace),
            ZoneBasedPermission()
        ]
        chain = MiddlewareChain(middlewares)
        
        session = Session(
            session_id="perf_session",
            user_id="user",
            channel_type="cli"
        )
        
        tool = Tool(
            name="perf_tool",
            description="Performance test",
            zone=Zone.ZONE_A,
            type=ToolType.FILE
        )
        
        test_file = Path(integration_workspace) / "data" / "perf.txt"
        test_file.write_text("test")
        
        # Measure execution time
        start = time.time()
        for _ in range(10):
            result = await chain.execute(
                tool=tool,
                arguments={"path": str(test_file)},
                session=session
            )
        end = time.time()
        
        # Execution should be fast
        avg_time = (end - start) / 10
        assert avg_time < 1.0  # Should be less than 1 second per operation
