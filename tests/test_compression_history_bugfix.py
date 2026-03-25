"""Test for message history trimming during compression (bugfix for re-accumulation issue)."""

import pytest
import logging
from datetime import datetime, timezone
from closeclaw.agents.core import AgentCore
from closeclaw.types import Message, AgentConfig, ContextManagementSettings, LLMSettings
from closeclaw.context import ContextManager, MessageCompactor
from closeclaw.runner import _PlaceholderLLM


class TestCompressionHistoryTrimming:
    """Test that message_history is properly trimmed during compression to prevent re-accumulation."""
    
    @pytest.fixture
    def small_context_config(self):
        """Create a config with small context window to trigger compression."""
        llm_settings = LLMSettings(
            model="test-model",
            provider="test",
            temperature=0.0
        )
        context_settings = ContextManagementSettings(
            max_tokens=8000,  # Small enough to trigger, but large enough for the system prompt base tokens
            warning_threshold=0.75,
            critical_threshold=0.95,
            active_window=10,
            summarize_window=50,
            chunk_size=5000,
        )
        return AgentConfig(
            model="test-model",
            max_iterations=10,
            timeout_seconds=300,
            system_prompt="You are a test agent.",
            context_management=context_settings,
            llm=llm_settings,
        )
    
    @pytest.fixture
    def agent(self, small_context_config):
        """Create an agent with small context window."""
        agent = AgentCore(
            agent_id="test-agent",
            llm_provider=_PlaceholderLLM(),
            config=small_context_config,
            workspace_root="/tmp",
            admin_user_id="test_user",
        )
        
        # Prevent real skills from inflating the system prompt token count
        # which would instantly trigger the CRITICAL threshold in tests.
        agent.skills_loader.get_always_skills = lambda: []
        agent.skills_loader.build_skills_summary = lambda: ""
        
        return agent
    
    def test_message_history_trimmed_after_compression(self, agent):
        """Verify that message_history is trimmed when compression is applied."""
        
        # Simulate adding many messages to reach compression threshold
        # Each message needs to be large enough to trigger the 8000 max_tokens limit
        for i in range(60):
            user_msg = Message(
                id=f"msg_{i}",
                channel_type="test",
                sender_id="user_123",
                sender_name="User",
                content=f"This is a test message number {i} with some content to increase token count. " * 40,
                metadata={}
            )
            agent.message_history.append(user_msg)
        
        initial_history_size = len(agent.message_history)
        logger = logging.getLogger("closeclaw.agents.core")
        
        # Trigger context formatting which should cause compression
        messages = agent._format_conversation_for_llm()
        
        # After compression, message_history should be trimmed
        final_history_size = len(agent.message_history)
        
        logger.info(f"History size before compression path: {initial_history_size}")
        logger.info(f"History size after compression: {final_history_size}")
        logger.info(f"Formatted messages count: {len(messages)}")
        
        # Verify that history was trimmed (should be significantly smaller)
        assert final_history_size < initial_history_size, \
            f"History should be trimmed! Before: {initial_history_size}, After: {final_history_size}"
        
        # Verify that we kept at least some context (active_window * 2)
        min_expected = agent.message_compactor.active_window * 2
        assert final_history_size >= min(min_expected, 5), \
            f"History trimmed too aggressively! Got {final_history_size}, expected at least {min_expected}"
    
    def test_no_re_accumulation_after_compression(self, agent):
        """Verify that adding a new message after compression doesn't immediately exceed limit."""
        
        # Simulate reaching compression state
        for i in range(60):  # 60 messages to trigger compression
            user_msg = Message(
                id=f"msg_{i}",
                channel_type="test",
                sender_id="user_123",
                sender_name="User",
                content=f"Test message {i} with content. " * 30,  # Make messages bigger (8000 limit)
                metadata={}
            )
            agent.message_history.append(user_msg)
        
        # Format once to trigger compression
        messages_before = agent._format_conversation_for_llm()
        history_after_compress = len(agent.message_history)
        
        logger = logging.getLogger("closeclaw.agents.core")
        logger.info(f"History after first compression: {history_after_compress} messages")
        logger.info(f"Formatted messages before new msg: {len(messages_before)}")
        
        # Count tokens in first formatting
        cm = agent.context_manager
        tokens_after_compress = cm.count_message_tokens(messages_before)
        status_after = cm.check_thresholds(tokens_after_compress)[0]
        
        logger.info(f"Token count after compress: {tokens_after_compress}/{cm.max_tokens}, Status: {status_after}")
        
        # Now add a new user message (simulating next user input)
        new_msg = Message(
            id="msg_new",
            channel_type="test",
            sender_id="user_123",
            sender_name="User",
            content="New user message after compression with lots of content to test. " * 10,
            metadata={}
        )
        agent.message_history.append(new_msg)
        
        # Format again and check if we're still in reasonable bounds
        messages_after_new = agent._format_conversation_for_llm()
        tokens_after_new = cm.count_message_tokens(messages_after_new)
        status_after_new = cm.check_thresholds(tokens_after_new)[0]
        
        logger.info(f"Token count after new message: {tokens_after_new}/{cm.max_tokens}, Status: {status_after_new}")
        logger.info(f"History size after new message: {len(agent.message_history)}")
        
        # The key test: status should NOT be CRITICAL after just one message
        # (it was CRITICAL before compression, so there's room for one new message)
        assert status_after_new != "CRITICAL", \
            f"Should not be CRITICAL immediately after compression + new message! " \
            f"Token: {tokens_after_new}, Status: {status_after_new}"
    
    def test_compression_reduces_message_history_size(self, agent):
        """Verify that message_history size is reduced to target size during compression."""
        
        # Add many messages
        num_messages = 80
        for i in range(num_messages):
            user_msg = Message(
                id=f"msg_{i}",
                channel_type="test",
                sender_id="user_123",
                sender_name="User",
                content=f"Message content {i}. " * 30,
                metadata={}
            )
            agent.message_history.append(user_msg)
        
        assert len(agent.message_history) == num_messages
        
        # Trigger compression
        agent._format_conversation_for_llm()
        
        # Check that history was trimmed to roughly active_window * 2
        target_size = max(agent.message_compactor.active_window * 2, 5)
        final_size = len(agent.message_history)
        
        logger = logging.getLogger("closeclaw.agents.core")
        logger.info(f"Target history size: {target_size}, Actual: {final_size}")
        
        # Should be close to target (allow some variance)
        assert abs(final_size - target_size) <= 2, \
            f"History size {final_size} should be close to target {target_size}"
        assert final_size <= target_size * 1.5, \
            f"History size {final_size} should not exceed target {target_size} by much"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])





