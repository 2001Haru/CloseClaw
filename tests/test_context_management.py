"""Tests for Phase 4 Context Management."""

import pytest
from closeclaw.context import ContextManager, MessageCompactor


class TestContextManager:
    """Test token counting and context monitoring."""
    
    @pytest.fixture
    def context_manager(self):
        """Create a context manager for testing."""
        return ContextManager(
            max_tokens=100000,
            warning_threshold=0.75,
            critical_threshold=0.95,
            summarize_window=50,
            active_window=10
        )
    
    def test_token_counting_basic(self, context_manager):
        """Test basic token counting with fallback."""
        # With tiktoken available, this should be more accurate
        text = "Hello, this is a test message for token counting."
        token_count = context_manager.count_tokens(text)
        
        # Rough validation: should be between 5-15 tokens
        assert 5 <= token_count <= 15, f"Token count {token_count} out of expected range"
    
    def test_message_token_counting(self, context_manager):
        """Test counting tokens in a message list."""
        messages = [
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Paris."},
            {"role": "user", "content": "What is its population?"},
        ]
        
        total_tokens = context_manager.count_message_tokens(messages)
        assert total_tokens > 0, "Should count tokens for messages"
        
        # Verify it's accumulative
        individual_sum = sum(
            context_manager.count_tokens(msg.get('content', ''))
            for msg in messages
        )
        assert total_tokens >= individual_sum - 5  # Allow small variance
    
    def test_usage_ratio(self, context_manager):
        """Test usage ratio calculation."""
        # 50% usage
        ratio = context_manager.get_usage_ratio(50000)
        assert ratio == 0.5, "50,000/100,000 should be 0.5"
        
        # 100% usage
        ratio = context_manager.get_usage_ratio(100000)
        assert ratio == 1.0, "100,000/100,000 should be 1.0"
        
        # Over 100% (capped)
        ratio = context_manager.get_usage_ratio(120000)
        assert ratio == 1.0, "Should cap at 1.0"
    
    def test_threshold_checking_ok(self, context_manager):
        """Test threshold checking when usage is OK."""
        status, needs_flush = context_manager.check_thresholds(50000)  # 50%
        assert status == "OK"
        assert needs_flush is False
    
    def test_threshold_checking_warning(self, context_manager):
        """Test threshold checking at warning level."""
        # 75% of 100,000 = 75,000
        status, needs_flush = context_manager.check_thresholds(76000)
        assert status == "WARNING"
        assert needs_flush is True
    
    def test_threshold_checking_critical(self, context_manager):
        """Test threshold checking at critical level."""
        # 95% of 100,000 = 95,000
        status, needs_flush = context_manager.check_thresholds(96000)
        assert status == "CRITICAL"
        assert needs_flush is True
    
    def test_status_report(self, context_manager):
        """Test status report generation."""
        report = context_manager.get_status_report(75000)
        
        assert report["current_tokens"] == 75000
        assert report["max_tokens"] == 100000
        assert report["usage_ratio"] == 0.75
        assert report["status"] == "WARNING"
        assert report["needs_flush"] is True
        assert "timestamp" in report
    
    def test_json_report(self, context_manager):
        """Test JSON report generation."""
        import json
        json_str = context_manager.json_report(75000)
        report = json.loads(json_str)
        
        assert report["current_tokens"] == 75000
        assert report["status"] == "WARNING"


class TestMessageCompactor:
    """Test message compaction and summarization."""
    
    @pytest.fixture
    def compactor(self):
        """Create a message compactor for testing."""
        return MessageCompactor(
            summarize_window=50,
            active_window=10,
            chunk_size=5000
        )
    
    def test_identify_compactable_messages(self, compactor):
        """Test identifying compactable vs. active messages."""
        # Simulate 100 messages
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        to_summarize, to_keep = compactor.identify_compactable_messages(
            messages, 0, 0
        )
        
        # Should keep last 10 raw
        assert len(to_keep) == 10
        assert len(to_summarize) == 90
        assert to_keep == list(range(90, 100))
        assert to_summarize == list(range(0, 90))
    
    def test_extract_summary_content(self, compactor):
        """Test extracting content for summarization."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "How are you?"},
        ]
        
        content = compactor.extract_summary_content(messages, [0, 1, 2])
        
        assert "Hello" in content
        assert "Hi there" in content
        assert "How are you?" in content
        assert "[user]" in content
        assert "[assistant]" in content
    
    def test_create_summary_placeholder(self, compactor):
        """Test creating a summary message."""
        summary = compactor.create_summary_placeholder(
            original_count=50,
            summary_text="Discussion about Python",
            indices=list(range(50))
        )
        
        assert summary["role"] == "system"
        assert "CONTEXT_SUMMARY" in summary["content"]
        assert "50 rounds" in summary["content"]
        assert summary["is_summary"] is True
        assert summary["original_indices"] == list(range(50))
    
    def test_compact_messages_drop_oldest(self, compactor):
        """Test hard compaction (drop oldest)."""
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        compacted = compactor.compact_messages(messages, "drop_oldest")
        
        # Should only keep last 10
        assert len(compacted) == 10
        assert compacted[0]["content"] == "Message 90"
        assert compacted[-1]["content"] == "Message 99"
    
    def test_compact_messages_summarize(self, compactor):
        """Test soft compaction (summarize oldest)."""
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        compacted = compactor.compact_messages(messages, "summarize")
        
        # Should have: [summary message] + [last 10 raw messages]
        assert len(compacted) == 11  # 1 summary + 10 active
        assert compacted[0].get("is_summary") is True
        assert "[CONTEXT_SUMMARY]" in compacted[0]["content"]
    
    def test_apply_compression_strategy_ok(self, compactor):
        """Test compression strategy when usage is OK."""
        messages = [
            {"role": "user", "content": "Message"}
        ]
        
        result, action = compactor.apply_compression_strategy(
            messages, 50000, 0.5, force=False
        )
        
        # No compression needed at 50% usage
        assert len(result) == len(messages)
        assert action == "none"
    
    def test_apply_compression_strategy_warning(self, compactor):
        """Test compression strategy at warning threshold."""
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        result, action = compactor.apply_compression_strategy(
            messages, 80000, 0.8, force=False
        )
        
        # Should summarize
        assert action == "summarize"
        assert len(result) < len(messages)
    
    def test_apply_compression_strategy_critical(self, compactor):
        """Test compression strategy at critical threshold."""
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        result, action = compactor.apply_compression_strategy(
            messages, 98000, 0.98, force=False
        )
        
        # Should hard truncate
        assert action == "hard_truncate"
        assert len(result) == 10  # Only active window
    
    def test_apply_compression_strategy_forced(self, compactor):
        """Test forced truncation."""
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        result, action = compactor.apply_compression_strategy(
            messages, 50000, 0.5, force=True
        )
        
        # Should hard truncate even at low usage
        assert action == "hard_truncate"
        assert len(result) == 10
    
    def test_compaction_report(self, compactor):
        """Test compaction history reporting."""
        # Create a summary first
        msg = compactor.create_summary_placeholder(50, "Test", [])
        
        report = compactor.get_compaction_report()
        
        assert "total_summarizations" in report
        assert "recent_summaries" in report
        assert "config" in report
        assert report["config"]["active_window"] == 10


class TestContextIntegration:
    """Integration tests for context management."""
    
    def test_context_workflow(self):
        """Test a complete context management workflow."""
        cm = ContextManager(max_tokens=10000, warning_threshold=0.5)
        mc = MessageCompactor()
        
        # Simulate growing message history with longer content
        messages = []
        long_content = "The quick brown fox jumps over the lazy dog. " * 20  # ~900 chars
        for i in range(100):
            msg = {"role": "user" if i % 2 == 0 else "assistant", "content": f"Message {i}: {long_content}"}
            messages.append(msg)
        
        # Check token growth
        status, needs_flush = cm.check_thresholds(
            cm.count_message_tokens(messages)
        )
        
        # Should be over limit with 100 long messages
        assert needs_flush or status != "OK", f"Should exceed limit: status={status}, needs_flush={needs_flush}"
        
        # Apply compaction
        compressed, action = mc.apply_compression_strategy(
            messages,
            cm.token_count,
            cm.get_usage_ratio(cm.token_count)
        )
        
        # Should have compacted
        assert action != "none", "Should apply compression"
        assert len(compressed) < len(messages), "Should reduce message count"
    
    def test_token_counting_consistency(self):
        """Test that token counting is consistent across multiple calls."""
        cm = ContextManager()
        
        messages = [
            {"role": "user", "content": "What is the meaning of life?"},
            {"role": "assistant", "content": "The meaning of life is a profound question."},
        ]
        
        # Count multiple times
        count1 = cm.count_message_tokens(messages)
        count2 = cm.count_message_tokens(messages)
        count3 = cm.count_message_tokens(messages)
        
        # Should be identical
        assert count1 == count2 == count3
        assert count1 > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
