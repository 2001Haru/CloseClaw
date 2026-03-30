"""Tests for MemoryManager."""

import os
import shutil
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from closeclaw.memory.memory_manager import MemoryManager

@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace for testing."""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()
    yield str(workspace)
    shutil.rmtree(workspace)

@pytest.fixture
def memory_manager(temp_workspace):
    """Create a MemoryManager instance."""
    # Use a small model or mock for speed if possible, but FastEmbed is fast enough
    # We'll mock the embedding model to avoid downloading/loading in unit tests
    with patch("closeclaw.memory.memory_manager.TextEmbedding") as MockEmbedding:
        # Mock the embed method to return random vectors
        mock_instance = MockEmbedding.return_value
        
        def mock_embed(texts):
            # Return a generator of random vectors
            for _ in texts:
                yield np.random.rand(384).astype(np.float32)
        
        mock_instance.embed.side_effect = mock_embed
        
        manager = MemoryManager(workspace_root=temp_workspace)
        # Force the mock to be used
        manager._embedding_model = mock_instance
        
        yield manager

def test_init_db(memory_manager):
    """Test database initialization."""
    assert os.path.exists(memory_manager.db_path)
    assert os.path.dirname(memory_manager.db_path).endswith(os.path.join("test_workspace", "CloseClaw Memory"))
    
    import sqlite3
    conn = sqlite3.connect(memory_manager.db_path)
    try:
        cursor = conn.cursor()
        
        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        assert "memory_chunks" in tables
        assert "embedding_cache" in tables
        assert "memory_chunks_fts" in tables
    finally:
        conn.close()

def test_add_memory(memory_manager):
    """Test adding a memory chunk."""
    content = "This is a test memory."
    source = "test_source"
    session_id = "session_1"
    metadata = {"key": "value"}
    
    memory_id = memory_manager.add_memory(content, source, session_id, metadata)
    assert memory_id > 0
    
    # Verify insertion
    import sqlite3
    conn = sqlite3.connect(memory_manager.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT content, source, session_id FROM memory_chunks WHERE id=?", (memory_id,))
        row = cursor.fetchone()
        assert row[0] == content
        assert row[1] == source
        assert row[2] == session_id
        
        # Verify FTS trigger
        cursor.execute("SELECT content FROM memory_chunks_fts WHERE rowid=?", (memory_id,))
        fts_row = cursor.fetchone()
        assert fts_row[0] == content
    finally:
        conn.close()

def test_get_embedding_caching(memory_manager):
    """Test embedding caching mechanism."""
    text = "Cache me if you can"
    
    # First call - should generate and cache
    emb1 = memory_manager.get_embedding(text)
    
    # Verify cache entry
    import sqlite3
    conn = sqlite3.connect(memory_manager.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM embedding_cache")
        assert cursor.fetchone()[0] == 1
    finally:
        conn.close()
    
    # Second call - should retrieve from cache
    # We can verify this by checking if the mock was called again?
    # Or just check equality
    emb2 = memory_manager.get_embedding(text)
    
    np.testing.assert_array_equal(emb1, emb2)

def test_retrieve_memories(memory_manager):
    """Test hybrid retrieval."""
    # Add some memories
    memory_manager.add_memory("Python is a programming language", "doc1", "s1")
    memory_manager.add_memory("The sky is blue", "doc2", "s1")
    memory_manager.add_memory("Coding in Python is fun", "doc3", "s1")
    
    # Mock embedding for query to be similar to Python docs
    # Since we use random embeddings in mock, vector search won't be semantically accurate
    # But we can test the mechanics
    
    results = memory_manager.retrieve_memories("Python", top_k=2)
    
    assert len(results) <= 2
    # Should return MemoryChunk objects
    if results:
        assert hasattr(results[0], 'content')
        assert hasattr(results[0], 'score')

def test_clear_memory(memory_manager):
    """Test clearing all memories."""
    memory_manager.add_memory("Test", "src", "s1")
    memory_manager.clear_memory()
    
    import sqlite3
    conn = sqlite3.connect(memory_manager.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM memory_chunks")
        assert cursor.fetchone()[0] == 0
    finally:
        conn.close()


def test_memory_db_path_under_workspace_memory(temp_workspace):
    """Memory database should live under workspace_root/CloseClaw Memory."""
    with patch("closeclaw.memory.memory_manager.TextEmbedding") as MockEmbedding:
        mock_instance = MockEmbedding.return_value

        def mock_embed(texts):
            for _ in texts:
                yield np.random.rand(384).astype(np.float32)

        mock_instance.embed.side_effect = mock_embed

        manager = MemoryManager(workspace_root=temp_workspace)
        assert manager.db_path == os.path.join(temp_workspace, "CloseClaw Memory", "memory.sqlite")


def test_add_memory_deduplicates_cleaned_content(memory_manager):
    """Duplicate normalized content should not create extra rows."""
    first = memory_manager.add_memory("line1\n\n\nline2", "src", "s1")
    second = memory_manager.add_memory(" line1 \n\nline2 ", "src", "s1")

    assert first == second

    import sqlite3
    conn = sqlite3.connect(memory_manager.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memory_chunks")
        assert cursor.fetchone()[0] == 1
    finally:
        conn.close()


def test_sync_daily_memory_files_lazy_indexes_daily_files(memory_manager, temp_workspace):
    """Daily memory markdown files should be indexed into memory_chunks with file source."""
    memory_daily_dir = os.path.join(temp_workspace, "CloseClaw Memory", "memory")
    os.makedirs(memory_daily_dir, exist_ok=True)

    file_a = os.path.join(memory_daily_dir, "2026-03-29.md")
    file_b = os.path.join(memory_daily_dir, "2026-03-30.md")
    with open(file_a, "w", encoding="utf-8") as f:
        f.write("# Day A\n\nTask A done.")
    with open(file_b, "w", encoding="utf-8") as f:
        f.write("# Day B\n\nTask B pending.")

    stats = memory_manager.sync_daily_memory_files_lazy(max_files=10)
    assert stats["indexed_files"] >= 2
    assert stats["failed_files"] == 0

    import sqlite3
    conn = sqlite3.connect(memory_manager.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT source FROM memory_chunks WHERE source LIKE 'file:CloseClaw Memory/memory/%'")
        rows = [row[0] for row in cursor.fetchall()]
        assert any("2026-03-29.md" in source for source in rows)
        assert any("2026-03-30.md" in source for source in rows)
    finally:
        conn.close()


def test_sync_daily_memory_files_lazy_updates_modified_file(memory_manager, temp_workspace):
    """When daily memory file changes, lazy sync should refresh indexed chunks."""
    memory_daily_dir = os.path.join(temp_workspace, "CloseClaw Memory", "memory")
    os.makedirs(memory_daily_dir, exist_ok=True)

    file_path = os.path.join(memory_daily_dir, "2026-03-30.md")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Initial note")

    memory_manager.sync_daily_memory_files_lazy(max_files=10)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Updated note after edit")

    stats = memory_manager.sync_daily_memory_files_lazy(max_files=10)
    assert stats["indexed_files"] >= 1
    assert stats["failed_files"] == 0

    import sqlite3
    conn = sqlite3.connect(memory_manager.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT content FROM memory_chunks
            WHERE source LIKE 'file:CloseClaw Memory/memory/2026-03-30.md#chunk:%'
            """
        )
        chunks = [row[0] for row in cursor.fetchall()]
        assert chunks
        assert any("Updated note after edit" in c for c in chunks)
        assert all("Initial note" not in c for c in chunks)
    finally:
        conn.close()



