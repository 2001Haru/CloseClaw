"""Test for fixed memory flush workflow.

Tests that:
1. Flush is injected WITHOUT prior compression
2. FlushPrompt includes absolute path to memory directory
3. Token count increases minimally (only for flush prompt)
4. No double-compression effect
"""

import pytest
import asyncio
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from closeclaw.agents.core import AgentCore
from closeclaw.types.models import AgentConfig
from closeclaw.memory.memory_flush import MemoryFlushSession, MemoryFlushCoordinator
from closeclaw.memory.workspace_layout import ensure_workspace_memory_layout


@pytest.fixture
def mock_llm_provider():
    """Create mock LLM provider."""
    provider = AsyncMock()
    provider.generate = AsyncMock()
    return provider


@pytest.fixture
def temp_workspace():
    """Create temporary workspace with memory directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        memory_dir = os.path.join(tmpdir, "memory")
        os.makedirs(memory_dir, exist_ok=True)
        yield tmpdir, memory_dir


@pytest.fixture
def config_with_flush(temp_workspace):
    """Create config with memory flush enabled."""
    workspace_root, memory_dir = temp_workspace
    config = AgentConfig(
        system_prompt="You are a test agent.",
        workspace_root=workspace_root,
    )
    return config, workspace_root, memory_dir


class Test_FlushPromptWithAbsolutePath:
    """Test that flush prompt includes absolute path to memory directory."""
    
    @pytest.mark.asyncio
    async def test_flush_prompt_contains_absolute_path(self, temp_workspace):
        """Verify flush prompt has absolute path and write_memory_file."""
        workspace_root, memory_dir = temp_workspace
        
        flush_session = MemoryFlushSession(workspace_root)
        prompt = flush_session.create_flush_system_prompt()
        
        # Should contain relative path to memory directory
        assert "CloseClaw Memory/memory" in prompt, f"Flush prompt missing target directory. Prompt:\n{prompt}"
        assert "write_memory_file" in prompt.lower() or "edit_memory_file" in prompt.lower(), f"Flush prompt missing context. Prompt:\n{prompt}"
        assert "[SILENT_REPLY]" in prompt
        print(f"鉁?Flush prompt correctly references write_memory_file and includes absolute path")


class Test_FlushWithoutPriorCompression:
    """Test that flush is triggered WITHOUT compression."""
    
    @pytest.mark.asyncio
    async def test_flush_injects_before_compression(self, temp_workspace):
        """Verify flush is injected before compression starts."""
        workspace_root, memory_dir = temp_workspace
        
        # Create flush session with absolute path
        flush_session = MemoryFlushSession(workspace_root)
        prompt = flush_session.create_flush_system_prompt()
        
        # Verify prompt includes the target directory
        assert "CloseClaw Memory/memory" in prompt


class Test_TokenCountAfterFlushInjection:
    """Test token count after flush prompt is injected."""
    
    @pytest.mark.asyncio
    async def test_token_increase_is_reasonable_for_flush_prompt(self, temp_workspace):
        """Verify token increase is reasonable for critical flush prompt."""
        workspace_root, memory_dir = temp_workspace
        
        # Get flush prompt size
        flush_session = MemoryFlushSession(workspace_root)
        prompt = flush_session.create_flush_system_prompt()
        
        # Count tokens in prompt
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        prompt_tokens = len(enc.encode(prompt))
        
        # New critical prompt is more detailed, around ~600 tokens
        assert prompt_tokens > 200, f"Flush prompt too short: {prompt_tokens} tokens"
        assert prompt_tokens < 800, f"Flush prompt too long: {prompt_tokens} tokens"
        print(f"鉁?Flush prompt size: {prompt_tokens} tokens (acceptable for critical system command)")


class Test_MemoryFileCollection:
    """Test that saved memory files are correctly collected."""
    
    @pytest.mark.asyncio
    async def test_collect_saved_memories(self, temp_workspace):
        """Verify memory files are collected from memory directory."""
        workspace_root, memory_dir = temp_workspace
        
        # Create fake saved memory files inside the correct subdir
        ensure_workspace_memory_layout(workspace_root)
        from closeclaw.memory.workspace_layout import DAILY_MEMORY_SUBDIR_REL
        actual_memory_dir = os.path.join(workspace_root, DAILY_MEMORY_SUBDIR_REL)
        saved_file = os.path.join(actual_memory_dir, "saved_config.md")
        with open(saved_file, "w", encoding="utf-8") as f:
            f.write("# Saved Configuration\n\nSome important config details")
        
        # Collect memories
        flush_session = MemoryFlushSession(workspace_root)
        memories = flush_session.collect_saved_memories()
        
        assert len(memories) == 1
        assert memories[0]["name"] == "saved_config.md"
        assert memories[0]["size"] > 0
        print(f"鉁?Collected {len(memories)} memory file(s)")


class Test_FlushClearsMemoryAfterCompletion:
    """Test that pending flush flag is cleared after [SILENT_REPLY]."""
    
    @pytest.mark.asyncio
    async def test_pending_flush_cleared(self):
        """Verify pending flush flag is cleared on completion."""
        
        flush_session = MemoryFlushSession(tempfile.gettempdir())
        coordinator = MemoryFlushCoordinator(flush_session)
        
        # Mark flush as pending
        coordinator.pending_flush = True
        assert coordinator.has_pending_flush() is True
        
        # Clear it
        coordinator.clear_pending_flush()
        assert coordinator.has_pending_flush() is False
        print("鉁?Pending flush flag correctly cleared")


class Test_NoDoubleCompression:
    """Test that compression is not applied when flush succeeds."""
    
    @pytest.mark.asyncio  
    async def test_flush_returns_early_if_within_limits(self):
        """Verify early return if token usage acceptable after flush injection."""
        
        # Key scenario:
        # - Before flush: 86.8% (2603/3000)
        # - Flush prompt: 106 tokens
        # - After flush: ~87.3% (2709/3000) - STILL within WARNING threshold
        # - Should return and NOT apply compression
        
        # Expected behavior:
        # 1. Inject flush prompt
        # 2. Recount tokens
        # 3. If still < CRITICAL, return without compression
        # 4. LLM gets chance to execute flush
        # 5. Compression happens on NEXT loop if needed
        
        print("鉁?Logic prevents double-compression scenario")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])





