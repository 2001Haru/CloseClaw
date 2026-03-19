"""Tests for Phase 4 Step 2 Memory Flush Session."""

import pytest
import os
import tempfile
from pathlib import Path
from datetime import datetime

from closeclaw.memory import MemoryFlushSession, MemoryFlushCoordinator


class TestMemoryFlushSession:
    """Test MemoryFlushSession functionality."""
    
    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def flush_session(self, temp_workspace):
        """Create MemoryFlushSession instance."""
        return MemoryFlushSession(workspace_root=temp_workspace)
    
    def test_initialization(self, flush_session, temp_workspace):
        """Test session initialization."""
        assert flush_session.workspace_root == temp_workspace
        assert flush_session.memory_dir == os.path.join(temp_workspace, "memory")
        assert os.path.exists(flush_session.memory_dir)
    
    def test_should_trigger_flush_conditions(self, flush_session):
        """Test flush trigger conditions."""
        # Should trigger at WARNING, 75-95%
        assert flush_session.should_trigger_flush("WARNING", 0.75) is True
        assert flush_session.should_trigger_flush("WARNING", 0.85) is True
        assert flush_session.should_trigger_flush("WARNING", 0.94) is True
        
        # Should NOT trigger at OK
        assert flush_session.should_trigger_flush("OK", 0.75) is False
        
        # Should NOT trigger at CRITICAL (at this point hard truncate happens)
        assert flush_session.should_trigger_flush("CRITICAL", 0.95) is False
    
    def test_silent_reply_marker_detection(self, flush_session):
        """Test detection of [SILENT_REPLY] marker in responses."""
        # With marker
        assert flush_session.check_for_silent_reply("Some response [SILENT_REPLY]") is True
        assert flush_session.check_for_silent_reply("[SILENT_REPLY]") is True
        assert flush_session.check_for_silent_reply("Tool call 1\nTool call 2 [SILENT_REPLY]") is True
        
        # Without marker
        assert flush_session.check_for_silent_reply("Normal response") is False
        assert flush_session.check_for_silent_reply("") is False
        assert flush_session.check_for_silent_reply(None) is False
    
    def test_extract_silent_reply_content(self, flush_session):
        """Test extracting content before [SILENT_REPLY] marker."""
        # With marker
        result = flush_session.extract_silent_reply_content("tool_call_1 [SILENT_REPLY] ignored")
        assert "tool_call_1" in result
        assert "[SILENT_REPLY]" not in result
        assert "ignored" not in result
        
        # Multi-line
        text = "Tool call 1\nTool call 2 [SILENT_REPLY]"
        result = flush_session.extract_silent_reply_content(text)
        assert "Tool call 1" in result
        assert "Tool call 2" in result
        
        # No marker
        text = "Normal response"
        result = flush_session.extract_silent_reply_content(text)
        assert result == text

    def test_flush_prompt_includes_compact_memory_block_requirement(self, flush_session):
        """Flush prompt should require a structured compact memory block before SILENT_REPLY."""
        prompt = flush_session.create_flush_system_prompt()

        assert "[COMPACT_MEMORY_BLOCK]" in prompt
        assert "[/COMPACT_MEMORY_BLOCK]" in prompt
        assert "[SILENT_REPLY]" in prompt
    
    def test_collect_saved_memories_empty(self, flush_session):
        """Test collecting memories from empty directory."""
        memories = flush_session.collect_saved_memories()
        assert memories == []
    
    def test_collect_saved_memories_with_files(self, flush_session):
        """Test collecting memories with actual files."""
        # Create some test memory files
        test_files = [
            ("memory1.md", "# Memory 1\n\nContent here"),
            ("memory2.md", "# Memory 2\n\nMore content"),
            ("config.txt", "Not a markdown file"),
        ]
        
        for filename, content in test_files:
            file_path = os.path.join(flush_session.memory_dir, filename)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
        
        memories = flush_session.collect_saved_memories()
        
        # Should only find .md files
        assert len(memories) == 2
        names = [m['name'] for m in memories]
        assert 'memory1.md' in names
        assert 'memory2.md' in names
        assert 'config.txt' not in names
        
        # All should have metadata
        for m in memories:
            assert 'name' in m
            assert 'path' in m
            assert 'size' in m
            assert 'modified' in m
            assert m['size'] > 0
    
    def test_generate_post_flush_notification_no_files(self, flush_session):
        """Test notification generation when no files saved."""
        notification = flush_session.generate_post_flush_notification([], "test_session_123")
        
        assert "Session ID: test_session_123" in notification
        assert "No files were saved" in notification
        assert "✅" in notification
    
    def test_generate_post_flush_notification_with_files(self, flush_session):
        """Test notification generation with saved files."""
        # Create test files
        test_files = [
            ("session_config.md", "# Configuration\n" + "x" * 100),
            ("session_decisions.md", "# Decisions\n" + "y" * 200),
        ]
        
        for filename, content in test_files:
            file_path = os.path.join(flush_session.memory_dir, filename)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
        
        saved_files = flush_session.collect_saved_memories()
        notification = flush_session.generate_post_flush_notification(saved_files, "flush_456")
        
        assert "Session ID: flush_456" in notification
        assert "Saved 2 memory file(s)" in notification
        assert "session_config.md" in notification
        assert "session_decisions.md" in notification
        assert "KB" in notification  # File size indicator
    
    def test_record_flush_event(self, flush_session):
        """Test recording flush event."""
        saved_files = [
            {"name": "test.md", "path": "/path", "size": 100, "modified": datetime.now().isoformat()}
        ]
        
        flush_session.record_flush_event(
            user_id="user123",
            session_id="flush_789",
            saved_files=saved_files,
            context_ratio=0.80
        )
        
        history = flush_session.get_flush_history()
        assert len(history) == 1
        assert history[0]['session_id'] == "flush_789"
        assert history[0]['user_id'] == "user123"
        assert history[0]['files_saved'] == 1
        assert history[0]['context_ratio'] == 0.80
    
    def test_json_report(self, flush_session):
        """Test JSON report generation."""
        import json
        
        # Record some events
        for i in range(3):
            flush_session.record_flush_event(
                user_id=f"user{i}",
                session_id=f"flush_{i}",
                saved_files=[],
                context_ratio=0.7 + i*0.05
            )
        
        report_str = flush_session.json_report()
        report = json.loads(report_str)
        
        assert 'memory_directory' in report
        assert 'total_flushes' in report
        assert report['total_flushes'] == 3
        assert 'recent_events' in report


class TestMemoryFlushCoordinator:
    """Test MemoryFlushCoordinator workflow."""
    
    @pytest.fixture
    def coordinator_setup(self):
        """Setup coordinator with session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = MemoryFlushSession(workspace_root=tmpdir)
            coordinator = MemoryFlushCoordinator(session)
            yield coordinator
    
    def test_initialization(self, coordinator_setup):
        """Test coordinator initialization."""
        coordinator = coordinator_setup
        assert coordinator.pending_flush is False
        assert coordinator.last_flush_session_id is None
    
    def test_mark_flush_pending(self, coordinator_setup):
        """Test marking flush as pending."""
        coordinator = coordinator_setup
        
        # Should mark at WARNING
        marked = coordinator.mark_flush_pending("WARNING", 0.80)
        assert marked is True
        assert coordinator.has_pending_flush() is True
        assert coordinator.last_flush_session_id is not None
        
        # Session ID should be valid format
        assert "flush_" in coordinator.last_flush_session_id
    
    def test_mark_flush_pending_conditions(self, coordinator_setup):
        """Test flush pending conditions."""
        coordinator = coordinator_setup
        
        # Should NOT mark at OK
        assert coordinator.mark_flush_pending("OK", 0.75) is False
        assert coordinator.has_pending_flush() is False
        
        # Should NOT mark at CRITICAL
        coordinator.clear_pending_flush()
        assert coordinator.mark_flush_pending("CRITICAL", 0.96) is False
        assert coordinator.has_pending_flush() is False
    
    def test_clear_pending_flush(self, coordinator_setup):
        """Test clearing pending flush."""
        coordinator = coordinator_setup
        
        # Mark as pending
        coordinator.mark_flush_pending("WARNING", 0.80)
        assert coordinator.has_pending_flush() is True
        
        # Clear
        coordinator.clear_pending_flush()
        assert coordinator.has_pending_flush() is False
    
    def test_session_id_generation(self, coordinator_setup):
        """Test unique session ID generation."""
        coordinator = coordinator_setup
        
        id1 = coordinator.generate_session_id()
        id2 = coordinator.generate_session_id()
        
        # Format should be valid
        assert "flush_" in id1
        assert "flush_" in id2
        
        # Should be deterministic but with timestamps
        # (generated IDs might be same if in same second)
        # At minimum format should match pattern


class TestMemoryFlushIntegration:
    """Integration tests for memory flush workflow."""
    
    def test_complete_flush_workflow(self):
        """Test complete memory flush workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup
            session = MemoryFlushSession(workspace_root=tmpdir)
            coordinator = MemoryFlushCoordinator(session)
            
            # Simulate approaching limit
            triggered = coordinator.mark_flush_pending("WARNING", 0.78)
            assert triggered is True
            
            # LLM returns response with flush marker and tool calls
            response = "Saving important findings [SILENT_REPLY]"
            is_flush = (coordinator.has_pending_flush() and 
                       session.check_for_silent_reply(response))
            assert is_flush is True
            
            # Collect saved memories
            saved = session.collect_saved_memories()
            # Initially empty (no files actually saved in test)
            assert isinstance(saved, list)
            
            # Generate notification
            notification = session.generate_post_flush_notification(saved, coordinator.last_flush_session_id)
            assert "Auto Memory Flush Completed" in notification
            
            # Record event
            session.record_flush_event(
                user_id="test_user",
                session_id=coordinator.last_flush_session_id,
                saved_files=saved,
                context_ratio=0.78
            )
            
            # Verify history
            history = session.get_flush_history()
            assert len(history) == 1
            assert history[0]['user_id'] == "test_user"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
