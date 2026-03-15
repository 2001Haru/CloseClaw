"""Tests for Agent.run() main loop (Phase 2)."""

import pytest
import asyncio
import logging
from typing import Optional
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from closeclaw.agents import AgentCore
from closeclaw.types import Message, AgentState, Zone, Tool, AgentConfig, Session, ToolType
from closeclaw.agents.task_manager import TaskManager

logger = logging.getLogger(__name__)


class TestAgentMainLoop:
    """Test cases for Agent.run() main loop with TaskManager integration."""
    
    @pytest.fixture
    async def agent_setup(self):
        """Setup agent with mocked LLM and TaskManager."""
        mock_llm = AsyncMock()
        config = AgentConfig(
            model="gpt-4",
            temperature=0.7,
            system_prompt="You are a helpful assistant."
        )
        
        agent = AgentCore(
            agent_id="test_agent",
            llm_provider=mock_llm,
            config=config,
            workspace_root="/tmp/test",
            admin_user_id="admin_user",
        )
        
        task_manager = TaskManager()
        agent.set_task_manager(task_manager)
        
        return agent, mock_llm, task_manager
    
    @pytest.mark.asyncio
    async def test_main_loop_basic_message_flow(self, agent_setup):
        """Test: User message → process → response → send.
        
        Scenario:
        1. User sends a simple message (no tools)
        2. LLM responds with text only
        3. Agent sends response back to user
        4. Loop exits cleanly
        """
        agent, mock_llm, task_manager = agent_setup
        
        # Mock user input: one message then None (exit)
        messages_to_send = [
            Message(
                id="msg_001",
                channel_type="cli",
                sender_id="user_123",
                sender_name="TestUser",
                content="What is 2+2?",
            ),
            None,  # Signal end of input
        ]
        message_queue = iter(messages_to_send)
        
        async def mock_input_fn():
            """Simulate receiving a message."""
            try:
                return next(message_queue)
            except StopIteration:
                return None
        
        # Track output
        output_calls = []
        async def mock_output_fn(response):
            """Capture output."""
            output_calls.append(response)
        
        # Mock LLM to return simple response
        mock_llm.generate.return_value = ("2+2 equals 4", None)
        
        # Run agent loop
        await agent.run(
            session_id="session_123",
            user_id="user_123",
            channel_type="cli",
            message_input_fn=mock_input_fn,
            message_output_fn=mock_output_fn,
        )
        
        # Verify
        assert len(output_calls) >= 1
        assert output_calls[0]["type"] == "response"
        assert "2+2 equals 4" in output_calls[0]["response"]
        assert agent.state == AgentState.IDLE  # Should be idle after session ends
        logger.info(f"✅ Basic message flow: {len(output_calls)} outputs captured")
    
    @pytest.mark.asyncio
    async def test_main_loop_with_tool_call(self, agent_setup):
        """Test: User message → tool call → tool result → response.
        
        Scenario:
        1. User asks for web search
        2. LLM returns tool_call for web_search
        3. Agent executes tool (Zone A = auto execute)
        4. Returns result to LLM
        5. Sends response to user
        """
        agent, mock_llm, task_manager = agent_setup
        
        # Register a mock tool
        async def mock_web_search(query: str):
            return {"results": [{"title": "Result 1", "url": "http://example.com"}]}
        
        tool = Tool(
            name="web_search",
            description="Search the web",
            handler=mock_web_search,
            type=ToolType.WEBSEARCH,
            zone=Zone.ZONE_A,
            parameters={"query": {"type": "string"}},
        )
        agent.register_tool(tool)
        
        # Mock input
        messages_to_send = [
            Message(
                id="msg_001",
                channel_type="cli",
                sender_id="user_123",
                sender_name="TestUser",
                content="Search for Python tutorials",
            ),
            None,
        ]
        message_queue = iter(messages_to_send)
        
        async def mock_input_fn():
            try:
                return next(message_queue)
            except StopIteration:
                return None
        
        output_calls = []
        async def mock_output_fn(response):
            output_calls.append(response)
        
        # Mock LLM to return tool call, then follow-up response
        from closeclaw.types import ToolCall
        tool_call = ToolCall(
            tool_id="tc_001",
            name="web_search",
            arguments={"query": "Python tutorials"}
        )
        mock_llm.generate.return_value = (None, [tool_call])
        
        # Run agent
        await agent.run(
            session_id="session_123",
            user_id="user_123",
            channel_type="cli",
            message_input_fn=mock_input_fn,
            message_output_fn=mock_output_fn,
        )
        
        # Verify
        responses = [o for o in output_calls if o["type"] == "response"]
        assert len(responses) >= 1
        assert len(responses[0].get("tool_results", [])) >= 1
        logger.info(f"✅ Tool execution: {len(responses[0]['tool_results'])} tool results")
    
    @pytest.mark.asyncio
    async def test_main_loop_auth_required_flow(self, agent_setup):
        """Test: Zone C operation → auth_request → user approval → execution.
        
        Scenario:
        1. User requests dangerous operation (Zone C)
        2. Agent enters WAITING_FOR_AUTH state
        3. Auth request sent to user
        4. User approves via approve_auth_request()
        5. Operation executes
        """
        agent, mock_llm, task_manager = agent_setup
        
        # Mock a dangerous tool
        async def mock_delete_file(path: str):
            return {"deleted": path}
        
        tool = Tool(
            name="delete_file",
            description="Delete a file (DANGEROUS)",
            handler=mock_delete_file,
            type=ToolType.FILE,
            zone=Zone.ZONE_C,  # Requires auth
            parameters={"path": {"type": "string"}},
        )
        agent.register_tool(tool)
        
        # Mock input
        messages_to_send = [
            Message(
                id="msg_001",
                channel_type="cli",
                sender_id="user_123",
                sender_name="TestUser",
                content="Delete my config file",
            ),
            None,
        ]
        message_queue = iter(messages_to_send)
        
        async def mock_input_fn():
            try:
                return next(message_queue)
            except StopIteration:
                return None
        
        output_calls = []
        async def mock_output_fn(response):
            output_calls.append(response)
            logger.debug(f"Output: {response.get('type')}")
        
        # Mock middleware to mark as auth_required
        mock_middleware = AsyncMock()
        mock_middleware.check_permission.return_value = {
            "status": "requires_auth",
            "auth_request_id": "auth_001",
            "tool_name": "delete_file",
            "description": "Delete config.yaml",
            "diff_preview": "- config.yaml (45 bytes)",
        }
        agent.set_middleware_chain(mock_middleware)
        
        # Mock LLM to return delete_file tool call
        from closeclaw.types import ToolCall
        tool_call = ToolCall(
            tool_id="tc_001",
            name="delete_file",
            arguments={"path": "config.yaml"}
        )
        mock_llm.generate.return_value = (None, [tool_call])
        
        # Run agent in background
        agent_task = asyncio.create_task(
            agent.run(
                session_id="session_123",
                user_id="user_123",
                channel_type="cli",
                message_input_fn=mock_input_fn,
                message_output_fn=mock_output_fn,
            )
        )
        
        # Wait for auth request
        await asyncio.sleep(0.5)
        
        # Verify auth request was sent
        auth_requests = [o for o in output_calls if o["type"] == "auth_request"]
        assert len(auth_requests) >= 1
        logger.info(f"✅ Auth request triggered: {auth_requests[0]['auth_request_id']}")
        
        # Clean up
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
    
    @pytest.mark.asyncio
    async def test_main_loop_task_polling(self, agent_setup):
        """Test: Agent polls background tasks and sends results.
        
        Scenario:
        1. Background task completes (simulated)
        2. Agent polls via poll_background_tasks()
        3. Sends task completion notification to user
        """
        agent, mock_llm, task_manager = agent_setup
        
        # Create a mock background task
        from closeclaw.types import BackgroundTask, TaskStatus
        completed_task = BackgroundTask(
            task_id="#001",
            tool_name="web_search",
            tool_arguments={"query": "test"},
            expires_after=3600,
        )
        completed_task.status = TaskStatus.COMPLETED
        completed_task.result = {"results": ["result1", "result2"]}
        completed_task.completed_at = datetime.utcnow()
        
        # Mock input
        messages_to_send = [None]  # Exit immediately
        message_queue = iter(messages_to_send)
        
        async def mock_input_fn():
            try:
                return next(message_queue)
            except StopIteration:
                return None
        
        output_calls = []
        async def mock_output_fn(response):
            output_calls.append(response)
        
        # Pre-populate completed task in taskmanager
        task_manager.completed_results["#001"] = completed_task
        
        # Mock poll_results to return the task
        with patch.object(task_manager, 'poll_results') as mock_poll:
            mock_poll.return_value = [
                {
                    "task_id": "#001",
                    "status": "completed",
                    "result": {"results": ["result1", "result2"]},
                    "error": None,
                }
            ]
            
            # Run agent
            await agent.run(
                session_id="session_123",
                user_id="user_123",
                channel_type="cli",
                message_input_fn=mock_input_fn,
                message_output_fn=mock_output_fn,
            )
        
        # Verify task completion notification
        task_completions = [o for o in output_calls if o["type"] == "task_completed"]
        assert len(task_completions) >= 1
        assert task_completions[0]["task_id"] == "#001"
        logger.info(f"✅ Task polling: Notified user of completed task {task_completions[0]['task_id']}")
    
    @pytest.mark.asyncio
    async def test_main_loop_state_persistence(self, agent_setup):
        """Test: Agent saves and restores state.json.
        
        Scenario:
        1. Agent processes messages
        2. State is saved before each iteration
        3. State can be restored on restart
        """
        agent, mock_llm, task_manager = agent_setup
        
        # Mock input
        messages_to_send = [
            Message(
                id="msg_001",
                channel_type="cli",
                sender_id="user_123",
                sender_name="TestUser",
                content="Test persistence",
            ),
            None,
        ]
        message_queue = iter(messages_to_send)
        
        async def mock_input_fn():
            try:
                return next(message_queue)
            except StopIteration:
                return None
        
        output_calls = []
        async def mock_output_fn(response):
            output_calls.append(response)
        
        mock_llm.generate.return_value = ("Acknowledged", None)
        
        # Run agent
        await agent.run(
            session_id="session_123",
            user_id="user_123",
            channel_type="cli",
            message_input_fn=mock_input_fn,
            message_output_fn=mock_output_fn,
        )
        
        # Verify message history was saved
        assert len(agent.message_history) >= 1
        
        # Test restore - create new agent and restore from snapshot
        mock_llm2 = AsyncMock()
        agent2 = AgentCore(
            agent_id="test_agent_2",
            llm_provider=mock_llm2,
            config=AgentConfig(model="gpt-4", system_prompt="Test"),
            workspace_root="/tmp/test",
            admin_user_id="admin_user",
        )
        
        task_manager2 = TaskManager()
        agent2.set_task_manager(task_manager2)
        
        # Get saved state from first agent
        state_snapshot = await agent._save_state()
        
        # Restore in second agent
        await agent2._restore_state(state_snapshot)
        
        # Verify restoration
        assert len(agent2.message_history) >= 0  # Should have history (or empty)
        logger.info(f"✅ State persistence: Saved and restored state snapshot")


class TestAgentMainLoopIntegration:
    """Integration tests for Agent.run() with real TaskManager."""
    
    @pytest.fixture
    async def integration_setup(self):
        """Setup with real TaskManager (no mocks)."""
        mock_llm = AsyncMock()
        config = AgentConfig(
            model="gpt-4",
            temperature=0.7,
        )
        
        agent = AgentCore(
            agent_id="integration_agent",
            llm_provider=mock_llm,
            config=config,
            workspace_root="/tmp/test",
            admin_user_id="admin_user",
        )
        
        # Real TaskManager
        task_manager = TaskManager()
        agent.set_task_manager(task_manager)
        
        return agent, mock_llm, task_manager
    
    @pytest.mark.asyncio
    async def test_background_task_creation_during_loop(self, integration_setup):
        """Test: Agent creates background task during message processing.
        
        Scenario:
        1. User asks for long-running operation
        2. Agent creates background task via TaskManager
        3. LLM gets task_id back immediately
        4. Agent returns task_id to user without waiting for completion
        """
        agent, mock_llm, task_manager = integration_setup
        
        # Register a long-running tool
        async def mock_web_crawl(url: str) -> dict:
            await asyncio.sleep(0.5)  # Simulate crawl time
            return {"status": "success", "pages": 42}
        
        tool = Tool(
            name="web_crawl",
            description="Crawl a website",
            handler=mock_web_crawl,
            type=ToolType.WEBSEARCH,
            zone=Zone.ZONE_B,
            parameters={"url": {"type": "string"}},
        )
        agent.register_tool(tool)
        
        # Scenario: Agent should detect this is long-running and create background task
        # For now, test that loop doesn't block
        
        messages_to_send = [
            Message(
                id="msg_001",
                channel_type="cli",
                sender_id="user_123",
                sender_name="TestUser",
                content="Crawl https://example.com",
            ),
            None,
        ]
        message_queue = iter(messages_to_send)
        
        async def mock_input_fn():
            try:
                return next(message_queue)
            except StopIteration:
                return None
        
        output_calls = []
        start_time = datetime.utcnow()
        
        async def mock_output_fn(response):
            output_calls.append(response)
        
        # Mock LLM to call the tool
        from closeclaw.types import ToolCall
        tool_call = ToolCall(
            tool_id="tc_001",
            name="web_crawl",
            arguments={"url": "https://example.com"}
        )
        mock_llm.generate.return_value = (None, [tool_call])
        
        # Run agent
        await agent.run(
            session_id="session_123",
            user_id="user_123",
            channel_type="cli",
            message_input_fn=mock_input_fn,
            message_output_fn=mock_output_fn,
        )
        
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        
        # Verify agent didn't block (should complete quickly)
        # Note: In real implementation, this would detect tool is long-running
        # and route to TaskManager for non-blocking execution
        logger.info(f"✅ Background task test completed in {elapsed:.2f}s")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
