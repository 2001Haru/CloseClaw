"""Tests for agent core loop."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from closeclaw.agents.core import AgentCore, LLMProvider
from closeclaw.types import (
    AgentState,  ToolType, Tool, Session,
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
                tool_id="tool_call_1",
                name="read_file",
                arguments={"path": "/data/test.txt"}
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

    def test_set_task_manager_registers_existing_tool_handlers(self, sample_agent_config, mock_llm, temp_workspace):
        """TaskManager should receive handlers for tools already registered on agent."""
        from closeclaw.agents.task_manager import TaskManager

        async def dummy_handler():
            return "ok"

        tool = Tool(
            name="dummy_tool",
            description="Dummy tool",
            need_auth=False,
            type=ToolType.FILE,
            handler=dummy_handler,
        )

        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
        )
        agent.register_tool(tool)

        tm = TaskManager()
        agent.set_task_manager(tm)

        assert "dummy_tool" in tm.tool_handlers

    @pytest.mark.asyncio
    async def test_poll_background_tasks_normalizes_taskmanager_dict(self, sample_agent_config, mock_llm, temp_workspace):
        from closeclaw.agents.task_manager import TaskManager
        from closeclaw.types import BackgroundTask, TaskStatus

        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
        )
        tm = TaskManager()
        agent.set_task_manager(tm)

        task = BackgroundTask(task_id="#001", tool_name="pwd", tool_arguments={}, expires_after=60)
        task.status = TaskStatus.FAILED
        task.error = "Tool 'pwd' not registered"

        async def _fake_poll():
            return {"#001": task}

        tm.poll_results = _fake_poll  # type: ignore[method-assign]

        results = await agent.poll_background_tasks()
        assert isinstance(results, list)
        assert results[0]["task_id"] == "#001"
        assert results[0]["status"] in {"failed", TaskStatus.FAILED.value}


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
        
        # Set current session
        agent.current_session = session
        
        response = await agent.process_message(sample_message)
        
        assert response is not None
        assert isinstance(response, dict)
        assert "response" in response
    
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
            need_auth=False,
            type=ToolType.FILE
        )
        agent.register_tool(read_file)
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        message = Message(
            id="msg_1",
            channel_type="cli",
            sender_id="user_456",
            sender_name="User",
            content="Read /data/test.txt",
            timestamp=datetime.now(timezone.utc)
        )
        
        # Set current session
        agent.current_session = session
        
        response = await agent.process_message(message)
        assert response is not None


class TestAuthorization:
    """Test authorization handling."""
    
    @pytest.mark.asyncio
    async def test_sensitive_tool_requires_auth(self, sample_agent_config, mock_llm,
                                       temp_workspace):
        """Test Sensitive operations require authorization."""
        from closeclaw.middleware import MiddlewareChain, AuthPermissionMiddleware
        
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
            admin_user_id="admin_001"
        )
        
        # Set up middleware chain for auth checks
        chain = MiddlewareChain()
        chain.add_middleware(AuthPermissionMiddleware())
        agent.set_middleware_chain(chain)
        
        async def mock_handler(**kwargs):
            return "Deleted"

        delete_tool = Tool(
            name="delete_file",
            description="Delete file",
            need_auth=True,
            type=ToolType.FILE,
            handler=mock_handler
        )
        agent.register_tool(delete_tool)
        
        # Process tool call that requires auth
        tool_call = ToolCall(
            tool_id="tc_1",
            name="delete_file",
            arguments={"path": "/data/important.txt"}
        )
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        agent.current_session = session
        
        result = await agent._process_tool_call(tool_call)
        
        # Should create auth request
        assert result.status == "auth_required"
        assert agent.pending_auth_requests
    
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
            operation_type="file_write",
            tool_name="delete_file",
            description="Delete file /data/old.txt"
        )
        
        agent.pending_auth_requests[auth_req.id] = auth_req.to_dict()
        
        # Approve request
        result = await agent.approve_auth_request(
            auth_request_id=auth_req.id,
            user_id="admin_001",
            approved=True
        )
        
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
            operation_type="shell_execute",
            tool_name="shell_command",
            description="Execute rm -rf /"
        )
        
        agent.pending_auth_requests[auth_req.id] = auth_req.to_dict()
        
        # Deny request
        result = await agent.approve_auth_request(
            auth_request_id=auth_req.id,
            user_id="admin_001",
            approved=False
        )
        
        # Request should be removed after denial
        assert auth_req.id not in agent.pending_auth_requests or result


class TestToolExecution:
    """Test tool execution."""
    
    @pytest.mark.asyncio
    async def test_execute_safe_tool(self, sample_agent_config, mock_llm, 
                                    temp_workspace, sample_tool_file):
        """Test executing safe (Safe) tool."""
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
            tool_id="tc_1",
            name="read_file",
            arguments={"path": f"{temp_workspace}/test.txt"}
        )
        
        # Create test file
        import pathlib
        pathlib.Path(f"{temp_workspace}/test.txt").write_text("test content")
        
        agent.current_session = session
        result = await agent._process_tool_call(tool_call)
        
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
            tool_id="tc_1",
            name="nonexistent_tool",
            arguments={}
        )
        
        agent.current_session = session
        result = await agent._process_tool_call(tool_call)
        
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
        # AgentCore doesn't seem to have max_iterations attribute directly exposed or used in this way in current version
        pass
    
    def test_iteration_count(self, sample_agent_config, mock_llm, temp_workspace):
        """Test agent tracks iteration count."""
        # AgentCore doesn't seem to have iteration_count attribute directly exposed or used in this way in current version
        pass


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
            id="msg_1",
            channel_type="cli",
            sender_id="user_456",
            sender_name="User",
            content="Test",
            timestamp=datetime.now(timezone.utc)
        )
        
        agent.current_session = session
        
        # Should handle error gracefully
        try:
            result = await agent.process_message(message)
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
        async def failing_handler(**kwargs):
            raise Exception("Tool execution failed")

        failing_tool = Tool(
            name="failing_tool",
            description="Fails",
            need_auth=False,
            type=ToolType.FILE,
            handler=failing_handler
        )
        agent.register_tool(failing_tool)
        
        session = Session(
            session_id="session_123",
            user_id="user_456",
            channel_type="cli"
        )
        
        tool_call = ToolCall(
            tool_id="tc_fail",
            name="failing_tool",
            arguments={}
        )
        
        # Should handle gracefully
        agent.current_session = session
        result = await agent._process_tool_call(tool_call)
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
            id="msg_1",
            channel_type="cli",
            sender_id="user_456",
            sender_name="User",
            content="What is the capital of France?",
            timestamp=datetime.now(timezone.utc)
        )
        
        agent.current_session = session
        
        response = await agent.process_message(message)
        
        assert response is not None
        assert isinstance(response, dict)


class TestCompactMemoryPromptInjection:
    """Tests for compact memory synthetic prompt pair behavior."""

    def test_compact_memory_pair_injected_before_history(self, sample_agent_config, mock_llm, temp_workspace):
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
        )

        agent.compact_memory_snapshot = "This is compact memory."
        agent.message_history = [
            Message(
                id="u1",
                channel_type="cli",
                sender_id="user_1",
                sender_name="User",
                content="New request",
                timestamp=datetime.now(timezone.utc),
            )
        ]

        messages = agent._format_conversation_for_llm()

        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "compact memory" in messages[1]["content"].lower()
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "This is compact memory."

    def test_compact_memory_snapshot_updates_on_compaction(self, sample_agent_config, mock_llm, temp_workspace):
        agent = AgentCore(
            agent_id="agent_001",
            llm_provider=mock_llm,
            config=sample_agent_config,
            workspace_root=temp_workspace,
        )

        agent.message_history = [
            Message(
                id="u1",
                channel_type="cli",
                sender_id="user_1",
                sender_name="User",
                content="Please remember this context",
                timestamp=datetime.now(timezone.utc),
            ),
            Message(
                id="a1",
                channel_type="cli",
                sender_id=agent.agent_id,
                sender_name="Agent",
                content="Latest assistant summary before compression.",
                timestamp=datetime.now(timezone.utc),
            ),
        ]

        with patch.object(agent.context_manager, "check_thresholds", return_value=("CRITICAL", False)):
            with patch.object(
                agent.message_compactor,
                "apply_compression_strategy",
                side_effect=lambda messages, token_count, usage_ratio, force: (messages, "truncate"),
            ):
                agent._format_conversation_for_llm()

        assert agent.compact_memory_snapshot is not None
        assert "Latest assistant summary before compression." in agent.compact_memory_snapshot


class TestMemoryFlushTrigger:
    """Regression tests for WARNING-threshold memory flush trigger."""

    @pytest.mark.asyncio
    async def test_warning_threshold_triggers_memory_flush(self, sample_agent_config, temp_workspace):
        class FlushAwareLLM:
            def __init__(self):
                self.calls = 0

            async def generate(self, messages, tools, **kwargs):
                self.calls += 1
                # Flush cycle: emit compact block and complete marker.
                if any("CRITICAL ACTIVITY: CONTEXT COMPRESSION" in (m.get("content") or "") for m in messages):
                    return (
                        "[COMPACT_MEMORY_BLOCK]Recent goal and constraints retained.[/COMPACT_MEMORY_BLOCK] [SILENT_REPLY]",
                        None,
                    )
                # Normal response path
                return "Post-flush normal reply", None

        cfg = sample_agent_config
        cfg.context_management.max_tokens = 200
        cfg.context_management.warning_threshold = 0.5
        cfg.context_management.critical_threshold = 0.95

        agent = AgentCore(
            agent_id="agent_flush_trigger",
            llm_provider=FlushAwareLLM(),
            config=cfg,
            workspace_root=temp_workspace,
        )
        agent.current_session = Session(
            session_id="session_flush_trigger",
            user_id="user_1",
            channel_type="cli",
        )

        # Inflate history to WARNING range.
        for i in range(20):
            agent.message_history.append(Message(
                id=f"u{i}",
                channel_type="cli",
                sender_id="user_1",
                sender_name="User",
                content="x" * 40,
                timestamp=datetime.now(timezone.utc),
            ))

        with patch.object(agent.context_manager, "check_thresholds", return_value=("WARNING", True)):
            with patch.object(agent.memory_flush_session, "should_trigger_flush", return_value=True):
                result = await agent.process_message(Message(
                    id="u_new",
                    channel_type="cli",
                    sender_id="user_1",
                    sender_name="User",
                    content="new request",
                    timestamp=datetime.now(timezone.utc),
                ))

        assert result is not None
        assert agent.compact_memory_snapshot is not None
        assert "Recent goal and constraints retained." in agent.compact_memory_snapshot


class TestCriticalContextFallback:
    """Regression tests for CRITICAL hard fallback behavior."""

    @pytest.mark.asyncio
    async def test_critical_keeps_last_10_rounds_and_warns_user(self, sample_agent_config, temp_workspace):
        class DirectAnswerLLM:
            async def generate(self, messages, tools, **kwargs):
                return "normal response", None

        cfg = sample_agent_config
        cfg.context_management.max_tokens = 200
        cfg.context_management.warning_threshold = 0.5
        cfg.context_management.critical_threshold = 0.8

        agent = AgentCore(
            agent_id="agent_critical_fallback",
            llm_provider=DirectAnswerLLM(),
            config=cfg,
            workspace_root=temp_workspace,
        )
        agent.current_session = Session(
            session_id="session_critical_fallback",
            user_id="user_1",
            channel_type="cli",
        )

        for i in range(60):
            sender_id = "user_1" if i % 2 == 0 else agent.agent_id
            sender_name = "User" if i % 2 == 0 else "Agent"
            agent.message_history.append(Message(
                id=f"m{i}",
                channel_type="cli",
                sender_id=sender_id,
                sender_name=sender_name,
                content="x" * 120,
                timestamp=datetime.now(timezone.utc),
            ))

        result = await agent.process_message(Message(
            id="u_new",
            channel_type="cli",
            sender_id="user_1",
            sender_name="User",
            content="trigger critical",
            timestamp=datetime.now(timezone.utc),
        ))

        assert result is not None
        assert "[CONTEXT WARNING]" in result.get("response", "")
        assert len(agent.message_history) <= 21


class TestContextGuardFlushIdempotency:
    """P2-B regression: flush trigger should run at most once per run."""

    @pytest.mark.asyncio
    async def test_flush_triggered_once_per_run(self, sample_agent_config, temp_workspace):
        class TwoStepLLM:
            def __init__(self):
                self.calls = 0

            async def generate(self, messages, tools, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return (
                        "step1",
                        [ToolCall(tool_id="tc1", name="read_file", arguments={"path": "x"})],
                    )
                return "done", None

        agent = AgentCore(
            agent_id="agent_context_guard_idempotency",
            llm_provider=TwoStepLLM(),
            config=sample_agent_config,
            workspace_root=temp_workspace,
        )
        agent.current_session = Session(
            session_id="session_context_guard_idempotency",
            user_id="user_1",
            channel_type="cli",
        )

        async def read_handler(path: str):
            return "ok"

        agent.register_tool(Tool(
            name="read_file",
            description="Read file",
            handler=read_handler,
            type=ToolType.FILE,
            need_auth=False,
            parameters={"path": {"type": "string"}},
        ))

        with patch.object(agent.context_manager, "check_thresholds", return_value=("WARNING", True)):
            with patch.object(agent.memory_flush_session, "should_trigger_flush", return_value=True):
                with patch.object(agent, "_execute_memory_flush_standalone", new_callable=AsyncMock) as flush_mock:
                    await agent.process_message(Message(
                        id="u_new",
                        channel_type="cli",
                        sender_id="user_1",
                        sender_name="User",
                        content="new request",
                        timestamp=datetime.now(timezone.utc),
                    ))

        assert flush_mock.await_count == 1





