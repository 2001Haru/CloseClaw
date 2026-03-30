"""Memory Manager for Phase 4 Step 3.

Implements SQLite-based long-term memory with hybrid search:
- Vector search (Semantic) using FastEmbed + K-NN
- Full-text search (Keyword) using SQLite FTS5
"""

import json
import logging
import os
import sqlite3
import time
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from fastembed import TextEmbedding

from .workspace_layout import memory_root_dir, ensure_workspace_memory_layout

logger = logging.getLogger(__name__)

class MemoryChunk:
    """Represents a retrieved memory chunk."""
    def __init__(
        self,
        id: int,
        content: str,
        score: float,
        source: str,
        timestamp: str,
        metadata: Dict[str, Any]
    ):
        self.id = id
        self.content = content
        self.score = score
        self.source = source
        self.timestamp = timestamp
        self.metadata = metadata

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "score": self.score,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }

class MemoryManager:
    """Manages long-term memory storage and retrieval."""

    def __init__(
        self,
        workspace_root: str,
        db_name: str = "memory.sqlite",
        embedding_model_name: str = "BAAI/bge-small-en-v1.5",
        use_gpu: bool = False
    ):
        """Initialize MemoryManager.

        Args:
            workspace_root: Root directory for storing the database
            db_name: Name of the SQLite database file
            embedding_model_name: Name of the FastEmbed model to use
            use_gpu: Whether to use GPU for embedding generation (if available)
        """
        self.workspace_root = os.path.abspath(workspace_root)
        # Phase4 patch: persist all memory/state assets under a unified workspace folder.
        ensure_workspace_memory_layout(self.workspace_root)
        self.memory_dir = memory_root_dir(self.workspace_root)
        self.db_path = os.path.join(self.memory_dir, db_name)
        self.embedding_model_name = embedding_model_name
        
        # Initialize embedding model lazily
        self._embedding_model = None
        self._use_gpu = use_gpu

        # Initialize database
        self._init_db()

    @property
    def embedding_model(self) -> TextEmbedding:
        """Lazy load the embedding model."""
        if self._embedding_model is None:
            logger.info(f"Loading FastEmbed model: {self.embedding_model_name}")
            try:
                self._embedding_model = TextEmbedding(
                    model_name=self.embedding_model_name,
                    providers=["CUDAExecutionProvider"] if self._use_gpu else ["CPUExecutionProvider"]
                )
            except Exception as e:
                logger.error(f"Failed to load FastEmbed model: {e}")
                # Fallback to CPU if GPU fails or other issues
                self._embedding_model = TextEmbedding(
                    model_name=self.embedding_model_name
                )
        return self._embedding_model

    def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        os.makedirs(self.memory_dir, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Enable FTS5 extension if not enabled (usually built-in)
        # Note: FTS5 is standard in Python's sqlite3 since 3.11+, but good to check
        
        # 1. memory_chunks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                content TEXT NOT NULL,
                embedding BLOB,
                source TEXT,
                metadata JSON
            )
        """)

        # 2. embedding_cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                content_hash TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 3. memory_chunks_fts virtual table
        # Check if table exists first to avoid error on re-creation
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_chunks_fts'")
        if not cursor.fetchone():
            # Use External Content FTS5 table to avoid data duplication
            cursor.execute("""
                CREATE VIRTUAL TABLE memory_chunks_fts USING fts5(
                    content,
                    content='memory_chunks',
                    content_rowid='id',
                    tokenize='porter'
                )
            """)
            
            # Create triggers to keep FTS index in sync
            cursor.execute("""
                CREATE TRIGGER memory_chunks_ai AFTER INSERT ON memory_chunks BEGIN
                    INSERT INTO memory_chunks_fts(rowid, content) VALUES (new.id, new.content);
                END;
            """)
            cursor.execute("""
                CREATE TRIGGER memory_chunks_ad AFTER DELETE ON memory_chunks BEGIN
                    INSERT INTO memory_chunks_fts(memory_chunks_fts, rowid, content) VALUES('delete', old.id, old.content);
                END;
            """)
            cursor.execute("""
                CREATE TRIGGER memory_chunks_au AFTER UPDATE ON memory_chunks BEGIN
                    INSERT INTO memory_chunks_fts(memory_chunks_fts, rowid, content) VALUES('delete', old.id, old.content);
                    INSERT INTO memory_chunks_fts(rowid, content) VALUES (new.id, new.content);
                END;
            """)

        # 4. memory_file_index table for lazy file-to-vector synchronization.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_file_index (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                content_hash TEXT,
                indexed_at DATETIME,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_file_index_status ON memory_file_index(status)")

        conn.commit()
        conn.close()

    def _get_content_hash(self, text: str) -> str:
        """Generate SHA256 hash for text content."""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    def _daily_memory_files(self) -> list[Path]:
        """Return sorted daily memory files: YYYY-MM-DD.md under CloseClaw Memory/memory."""
        daily_dir = Path(self.memory_dir) / "memory"
        if not daily_dir.exists() or not daily_dir.is_dir():
            return []

        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
        files = [p for p in daily_dir.iterdir() if p.is_file() and pattern.match(p.name)]
        # Newest date first for better practical recall freshness.
        files.sort(key=lambda p: p.name, reverse=True)
        return files

    def _source_from_path(self, file_path: Path, chunk_index: int) -> str:
        """Build source field that preserves exact originating file path."""
        relative_from_workspace = file_path.resolve().relative_to(Path(self.workspace_root).resolve())
        return f"file:{str(relative_from_workspace).replace(os.sep, '/')}#chunk:{chunk_index}"

    @staticmethod
    def _chunk_text(content: str, max_chars: int = 1200) -> list[str]:
        """Chunk markdown-ish text into medium blocks for retrieval quality."""
        normalized = content.replace("\r\n", "\n").strip()
        if not normalized:
            return []

        paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for para in paragraphs:
            plen = len(para)
            # Paragraph itself is too large: split hard by size.
            if plen > max_chars:
                if current:
                    chunks.append("\n\n".join(current).strip())
                    current = []
                    current_len = 0
                for start in range(0, plen, max_chars):
                    piece = para[start:start + max_chars].strip()
                    if piece:
                        chunks.append(piece)
                continue

            sep_cost = 2 if current else 0
            if current_len + sep_cost + plen > max_chars:
                chunks.append("\n\n".join(current).strip())
                current = [para]
                current_len = plen
            else:
                current.append(para)
                current_len += sep_cost + plen

        if current:
            chunks.append("\n\n".join(current).strip())
        return chunks

    def _delete_file_chunks(self, file_path: Path) -> None:
        """Delete previously indexed chunks for one file."""
        source_prefix = self._source_from_path(file_path, 0).rsplit("#chunk:", 1)[0]
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memory_chunks WHERE source LIKE ?", (f"{source_prefix}#chunk:%",))
            conn.commit()
        finally:
            conn.close()

    def _index_file_chunks(self, file_path: Path, content: str, content_hash: str) -> int:
        """Rebuild indexed chunks for one file and return inserted chunk count."""
        chunks = self._chunk_text(content)
        self._delete_file_chunks(file_path)
        if not chunks:
            return 0

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            inserted = 0
            for idx, chunk in enumerate(chunks, start=1):
                embedding = self.get_embedding(chunk)
                source = self._source_from_path(file_path, idx)
                metadata = json.dumps(
                    {
                        "index_type": "daily_memory_file",
                        "chunk_index": idx,
                        "chunk_count": len(chunks),
                        "file_hash": content_hash,
                    },
                    ensure_ascii=False,
                )
                cursor.execute(
                    """
                    INSERT INTO memory_chunks (session_id, content, embedding, source, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("__daily_memory_index__", chunk, embedding.tobytes(), source, metadata),
                )
                inserted += 1
            conn.commit()
            return inserted
        finally:
            conn.close()

    def sync_daily_memory_files_lazy(self, max_files: int = 3) -> dict[str, int]:
        """Lazy sync daily memory files into vector DB with bounded per-call work."""
        budget = max(1, int(max_files))
        files = self._daily_memory_files()
        file_map = {str(p.resolve()): p for p in files}
        now_iso = datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM memory_file_index")
            indexed_paths = {str(row["path"]) for row in cursor.fetchall()}

            # Drop stale index entries when files were removed.
            stale_paths = indexed_paths - set(file_map.keys())
            for stale_path in stale_paths:
                stale = Path(stale_path)
                try:
                    self._delete_file_chunks(stale)
                except Exception:
                    pass
                cursor.execute("DELETE FROM memory_file_index WHERE path = ?", (stale_path,))
            conn.commit()

            # Fetch current index rows after stale cleanup.
            cursor.execute("SELECT path, mtime, size, content_hash FROM memory_file_index")
            current_rows = {
                str(row["path"]): {
                    "mtime": float(row["mtime"]),
                    "size": int(row["size"]),
                    "content_hash": str(row["content_hash"] or ""),
                }
                for row in cursor.fetchall()
            }
        finally:
            conn.close()

        pending: list[Path] = []
        for path_str, path_obj in file_map.items():
            stat = path_obj.stat()
            row = current_rows.get(path_str)
            if row is None:
                pending.append(path_obj)
                continue
            if abs(float(stat.st_mtime) - row["mtime"]) > 1e-6 or int(stat.st_size) != row["size"]:
                pending.append(path_obj)

        indexed_files = 0
        indexed_chunks = 0
        failed_files = 0
        skipped_files = max(0, len(pending) - budget)

        for file_path in pending[:budget]:
            path_str = str(file_path.resolve())
            try:
                content = file_path.read_text(encoding="utf-8")
                content_hash = self._get_content_hash(content)
                stat = file_path.stat()

                previous_hash = current_rows.get(path_str, {}).get("content_hash", "")
                if previous_hash and previous_hash == content_hash:
                    conn = sqlite3.connect(self.db_path)
                    try:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            INSERT INTO memory_file_index(path, mtime, size, content_hash, indexed_at, status, error)
                            VALUES (?, ?, ?, ?, ?, 'synced', NULL)
                            ON CONFLICT(path) DO UPDATE SET
                                mtime=excluded.mtime,
                                size=excluded.size,
                                indexed_at=excluded.indexed_at,
                                status='synced',
                                error=NULL
                            """,
                            (path_str, float(stat.st_mtime), int(stat.st_size), content_hash, now_iso),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    indexed_files += 1
                    continue

                chunk_count = self._index_file_chunks(file_path, content, content_hash)
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO memory_file_index(path, mtime, size, content_hash, indexed_at, status, error)
                        VALUES (?, ?, ?, ?, ?, 'synced', NULL)
                        ON CONFLICT(path) DO UPDATE SET
                            mtime=excluded.mtime,
                            size=excluded.size,
                            content_hash=excluded.content_hash,
                            indexed_at=excluded.indexed_at,
                            status='synced',
                            error=NULL
                        """,
                        (path_str, float(stat.st_mtime), int(stat.st_size), content_hash, now_iso),
                    )
                    conn.commit()
                finally:
                    conn.close()

                indexed_files += 1
                indexed_chunks += chunk_count
            except Exception as exc:
                failed_files += 1
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO memory_file_index(path, mtime, size, content_hash, indexed_at, status, error)
                        VALUES (?, 0, 0, '', ?, 'error', ?)
                        ON CONFLICT(path) DO UPDATE SET
                            indexed_at=excluded.indexed_at,
                            status='error',
                            error=excluded.error
                        """,
                        (path_str, now_iso, str(exc)),
                    )
                    conn.commit()
                finally:
                    conn.close()
                logger.warning("Failed to sync daily memory file: %s (%s)", file_path, exc)

        return {
            "total_daily_files": len(files),
            "pending_files": len(pending),
            "indexed_files": indexed_files,
            "indexed_chunks": indexed_chunks,
            "failed_files": failed_files,
            "skipped_files": skipped_files,
        }

    def get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text, using cache if available."""
        content_hash = self._get_content_hash(text)
        
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT embedding FROM embedding_cache WHERE content_hash = ?", (content_hash,))
            row = cursor.fetchone()
            
            if row:
                # Deserialize from BLOB
                return np.frombuffer(row[0], dtype=np.float32)
            
            # Generate new embedding
            # FastEmbed returns a generator of embeddings, we take the first one
            embedding_gen = self.embedding_model.embed([text])
            embedding = next(embedding_gen)
            
            # Ensure it's float32 for consistency
            embedding = embedding.astype(np.float32)
            
            # Cache it
            cursor.execute(
                "INSERT OR REPLACE INTO embedding_cache (content_hash, embedding) VALUES (?, ?)",
                (content_hash, embedding.tobytes())
            )
            conn.commit()
            return embedding
        finally:
            conn.close()

    def add_memory(
        self,
        content: str,
        source: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Add a new memory chunk to the database.

        Args:
            content: The text content of the memory
            source: Source identifier (e.g. "file:summary.md")
            session_id: Session ID associated with this memory
            metadata: Optional metadata dictionary

        Returns:
            ID of the inserted memory chunk
        """
        content = self._normalize_memory_content(content)
        if not content:
            logger.warning("Attempted to add empty memory content")
            return -1

        dedup_id = self._find_duplicate_memory_id(content, source, session_id)
        if dedup_id is not None:
            logger.info("Skipped duplicate memory content for session=%s source=%s", session_id, source)
            return dedup_id

        embedding = self.get_embedding(content)
        metadata_json = json.dumps(metadata or {})
        
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO memory_chunks (session_id, content, embedding, source, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, content, embedding.tobytes(), source, metadata_json)
            )
            
            memory_id = cursor.lastrowid
            conn.commit()
            logger.info(f"Added memory chunk {memory_id} from {source}")
            return memory_id
        finally:
            conn.close()

    def _normalize_memory_content(self, content: str) -> str:
        """Apply lightweight cleanup before storing memory text."""
        lines = [line.rstrip() for line in content.replace("\r\n", "\n").split("\n")]
        compact: list[str] = []
        blank_streak = 0
        for line in lines:
            if line.strip() == "":
                blank_streak += 1
                if blank_streak > 1:
                    continue
                compact.append("")
                continue
            blank_streak = 0
            compact.append(line.strip())
        return "\n".join(compact).strip()

    def _find_duplicate_memory_id(self, content: str, source: str, session_id: str) -> Optional[int]:
        """Find an existing identical memory row to prevent duplicate writes."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM memory_chunks
                WHERE session_id = ? AND source = ? AND content = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id, source, content),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else None
        finally:
            conn.close()

    def retrieve_memories(
        self,
        query: str,
        top_k: int = 5,
        alpha: float = 0.4,  # Weight for vector search
        beta: float = 0.6,   # Weight for keyword search
        threshold: float = 0.3,
        session_id: Optional[str] = None
    ) -> List[MemoryChunk]:
        """Retrieve relevant memories using hybrid search.

        Args:
            query: Search query string
            top_k: Number of results to return
            alpha: Weight for vector similarity score (0-1)
            beta: Weight for keyword search score (0-1)
            threshold: Minimum final score threshold
            session_id: Optional filter by session ID

        Returns:
            List of MemoryChunk objects sorted by relevance
        """
        if not query.strip():
            return []

        query_embedding = self.get_embedding(query)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Vector Search (K-NN)
        # Fetch all embeddings to calculate cosine similarity in Python
        # For MVP with <1GB data, this is acceptable. For larger scale, use vector DB.
        
        sql = "SELECT id, content, embedding, source, timestamp, metadata FROM memory_chunks"
        params = []
        if session_id:
            sql += " WHERE session_id = ? OR session_id = ?"
            params.extend([session_id, "__daily_memory_index__"])
            
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        if not rows:
            conn.close()
            return []
            
        # Calculate vector scores
        vector_scores = {} # id -> score
        
        # Prepare matrix for batch calculation if possible, or loop
        # Looping is safer for variable memory usage
        for row in rows:
            if not row['embedding']:
                continue
                
            doc_embedding = np.frombuffer(row['embedding'], dtype=np.float32)
            
            # Cosine similarity: (A . B) / (||A|| * ||B||)
            # FastEmbed embeddings are normalized? Usually yes.
            # Let's assume normalized for now, or compute norm.
            # FastEmbed output is usually normalized.
            
            similarity = np.dot(query_embedding, doc_embedding)
            # Normalize if needed:
            # norm_q = np.linalg.norm(query_embedding)
            # norm_d = np.linalg.norm(doc_embedding)
            # similarity = similarity / (norm_q * norm_d)
            
            vector_scores[row['id']] = float(similarity)

        # 2. Keyword Search (FTS5)
        # FTS5 bm25() returns a negative value where more negative is better?
        # Or rank? Standard FTS5 'rank' column.
        # We'll use a simple query to get matches and assign a score.
        # Since FTS5 scoring is complex to normalize, we'll use a simplified approach:
        # If match, score = 1.0 (or scaled by rank).
        
        # Let's use the 'bm25' function if available or just rank.
        # Standard FTS5 query
        fts_scores = {} # id -> score
        
        # Escape query for FTS5 to avoid syntax errors
        safe_query = query.replace('"', '""')
        # Simple tokenization for query
        fts_query = f'"{safe_query}"' 
        
        try:
            # We join with memory_chunks to filter by session_id if needed
            # But FTS table uses rowid = memory_chunks.id
            
            fts_sql = """
                SELECT rowid, rank FROM memory_chunks_fts 
                WHERE memory_chunks_fts MATCH ? 
                ORDER BY rank
                LIMIT ?
            """
            # Note: FTS5 rank is usually lower is better.
            # We need to invert/normalize it.
            # For MVP, let's just say if it's in top 20 FTS results, it gets a score.
            
            cursor.execute(fts_sql, (fts_query, top_k * 2))
            fts_results = cursor.fetchall()
            
            for i, row in enumerate(fts_results):
                # Simple linear decay based on rank position
                # Rank 0 (best) -> Score 1.0
                # Rank N -> Score 0.0
                # This is a heuristic.
                score = 1.0 - (i / (len(fts_results) + 1))
                fts_scores[row['rowid']] = score
                
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 query failed: {e}")
            # Fallback: no keyword scores
            pass

        # 3. Hybrid Fusion
        final_results = []
        
        # Iterate over all docs that appeared in either search
        all_ids = set(vector_scores.keys()) | set(fts_scores.keys())
        
        for doc_id in all_ids:
            v_score = vector_scores.get(doc_id, 0.0)
            k_score = fts_scores.get(doc_id, 0.0)
            
            # Normalize vector score to 0-1 if it's cosine (-1 to 1)
            v_score = (v_score + 1) / 2
            
            final_score = (alpha * v_score) + (beta * k_score)
            
            if final_score >= threshold:
                # Retrieve full doc details from the rows we already fetched
                # Optimization: create a lookup dict from rows
                doc_row = next((r for r in rows if r['id'] == doc_id), None)
                if doc_row:
                    metadata = json.loads(doc_row['metadata']) if doc_row['metadata'] else {}
                    final_results.append(MemoryChunk(
                        id=doc_id,
                        content=doc_row['content'],
                        score=final_score,
                        source=doc_row['source'],
                        timestamp=doc_row['timestamp'],
                        metadata=metadata
                    ))
        
        # Sort by final score descending
        final_results.sort(key=lambda x: x.score, reverse=True)
        
        conn.close()
        return final_results[:top_k]

    def clear_memory(self) -> None:
        """Clear all memories (useful for testing)."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memory_chunks")
            cursor.execute("DELETE FROM embedding_cache")
            # FTS triggers will handle the virtual table
            conn.commit()
        finally:
            conn.close()
