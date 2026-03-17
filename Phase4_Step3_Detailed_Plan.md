# Phase 4 Step 3 详细计划书：SQLite 本地向量检索与混合记忆系统

**编制时间**: 2026-03-17
**目标定位**: 实现 CloseClaw 的长线记忆能力，通过本地 SQLite 数据库结合向量检索和全文检索，构建一个高效、低成本、可持久化的 RAG 知识库。

---

## 1. 核心目标与价值

*   **无限对话容量**: 将历史对话数据持久化存储，突破 LLM 上下文窗口限制。
*   **毫秒级检索**: 提供快速、准确的记忆检索能力，支持 Agent 在对话中实时获取相关信息。
*   **成本优化**: 通过 Embedding 缓存策略，显著降低外部 Embedding API 调用费用。
*   **智能上下文**: 结合语义相似度与关键词匹配，实现更智能、更精准的记忆召回。
*   **离线可用**: 本地 SQLite 存储，减少对外部服务的依赖，增强系统鲁棒性。

---

## 2. 核心组件与技术选型

### 2.1 数据库层：SQLite + Python 侧 K-NN + FTS5

*   **SQLite**: 作为轻量级、零运维的本地数据库，存储所有记忆数据。
*   **Python 侧 K-NN 检索**: 在 SQLite 中手动存储 BLOB 格式的向量 Embedding，并在 Python 代码中实现 K-NN (K-Nearest Neighbors) 相似度检索，以确保跨平台兼容性和稳定性。
*   **FTS5 (Full-Text Search)**: SQLite 内置的全文检索模块，用于关键词匹配和精确查找。

### 2.2 Embedding 层：本地 Embedding (FastEmbed) + 本地缓存

*   **本地 Embedding**: 优先使用 `FastEmbed` 库，它提供轻量级、无 PyTorch 依赖的 Embedding 模型，例如其默认的 `BGE-small-en-v1.5` 或其他适合的轻量级模型。
*   **外部 API (可选)**: 仍保留通过配置选择外部 Embedding API (如 OpenAI `text-embedding-3-small`) 的能力，以应对特定需求。
*   **Embedding 缓存**: 实现基于内容 Hash 的缓存机制，避免重复计算和 API 调用。

### 2.3 检索层：混合检索 (Hybrid Search)

*   **双路召回**: 同时执行向量相似度检索 (语义匹配) 和 FTS5 全文检索 (关键词匹配)。
*   **加权融合**: 对两种检索结果进行加权融合，生成最终的召回结果。

---

## 3. 详细实施计划

### 3.1 数据模型设计 (SQLite Schema)

我们将创建以下核心表：

#### `memory_chunks` 表 (存储记忆片段及其 Embedding)

```sql
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,           -- 关联的对话 Session ID
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, -- 记忆创建时间
    content TEXT NOT NULL,              -- 原始记忆内容 (例如：总结摘要、关键对话片段、文件内容)
    embedding BLOB,                     -- 记忆内容的向量 Embedding (BLOB 存储)
    source TEXT,                        -- 记忆来源 (e.g., "conversation_summary", "file:architecture_design.md")
    metadata JSON                       -- 额外元数据 (e.g., original_message_ids, file_path)
);
```

#### `embedding_cache` 表 (存储 Embedding 缓存)

```sql
CREATE TABLE embedding_cache (
    content_hash TEXT PRIMARY KEY,      -- 原始文本内容的 SHA256 Hash
    embedding BLOB NOT NULL,            -- 对应的向量 Embedding
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP -- 缓存时间
);
```

#### `memory_chunks_fts` 虚拟表 (FTS5 全文检索)

```sql
CREATE VIRTUAL TABLE memory_chunks_fts USING fts5(content, tokenize='porter');
-- 将 memory_chunks.content 字段同步到此表进行全文检索
```

### 3.2 Embedding 生成与缓存机制

1.  **`get_embedding(text: str)` 函数**:
    *   计算 `text` 的 SHA256 Hash。
    *   查询 `embedding_cache` 表，如果命中则直接返回缓存的 Embedding。
    *   如果未命中，调用外部 Embedding API (如 OpenAI) 生成 Embedding。
    *   将 `(content_hash, embedding)` 存入 `embedding_cache` 表。
    *   返回 Embedding。

2.  **集成到 Memory Flush 流程**:
    *   当 Memory Flush (Step 2) 诱导 LLM 生成 Markdown 文件并保存后，这些文件的内容将被读取。
    *   对每个文件的内容调用 `get_embedding` 生成 Embedding，并将其作为新的 `memory_chunk` 存入 `memory_chunks` 表。
    *   对于 Context Compaction (Step 1) 生成的总结摘要，也同样生成 Embedding 并存入 `memory_chunks`。

### 3.3 混合检索 (Hybrid Search) 实现

1.  **`retrieve_memories(query: str, top_k: int = 5) -> List[MemoryChunk]` 函数**:
    *   **步骤 1: 生成 Query Embedding**: 对 `query` 调用 `get_embedding` 生成查询向量。
    *   **步骤 2: 向量相似度检索 (Semantic Search)**:
        *   在 `memory_chunks` 表中执行 K-NN 搜索，找到与 Query Embedding 最相似的 `top_k` 个记忆片段。
        *   计算余弦相似度得分 `vector_similarity`。
    *   **步骤 3: 全文检索 (Keyword Search)**:
        *   在 `memory_chunks_fts` 虚拟表中执行 FTS5 搜索，找到与 `query` 关键词匹配的 `top_k` 个记忆片段。
        *   计算 BM25 或其他相关性得分 `bm25_score`。
    *   **步骤 4: 结果融合与排序**:
        *   将向量检索和全文检索的结果合并。
        *   对每个合并后的记忆片段，计算最终得分：`final_score = α * vector_similarity + β * bm25_score` (其中 `α=0.4, β=0.6` 为初始权重)。
        *   过滤掉 `final_score < threshold` (如 `0.3`) 的结果。
        *   按 `final_score` 降序排序，返回 `top_k` 个记忆片段。

### 3.4 AgentCore 集成与接口设计

1.  **`MemoryManager` 类**:
    *   新建 `closeclaw/memory/memory_manager.py`。
    *   封装 SQLite 数据库操作、Embedding 生成/缓存、混合检索逻辑。
    *   提供 `add_memory(content: str, source: str, session_id: str, metadata: dict)` 和 `retrieve_memories(query: str, session_id: Optional[str] = None)` 等接口。

2.  **`AgentCore` 修改**:
    *   在 `AgentCore.__init__` 中初始化 `MemoryManager`。
    *   修改 `_execute_memory_flush_with_context` 方法：在保存文件后，调用 `MemoryManager.add_memory` 将文件内容和总结摘要存入数据库。
    *   **引入新的 Tool**: `retrieve_memory(query: str)`。
        *   当 Agent 需要回忆历史信息时，可以主动调用此 Tool。
        *   此 Tool 将调用 `MemoryManager.retrieve_memories` 进行检索，并将结果返回给 Agent。
        *   检索结果将以简洁的格式注入到 LLM 的上下文，供其参考。

### 3.5 分库/分表策略 (初步考虑)

*   **单表优先**: 考虑到 `memory_chunks` 表在 1 年内 1000 对话量级下预计不会超过 1GB，初期可采用单表。
*   **未来扩展**: 若数据量剧增，可考虑按 `session_id` 或时间进行分表，但这不是 MVP 阶段的重点。

---

## 4. 验收标准与测试计划

*   **检索延迟**: 单次 `retrieve_memories` 调用 P99 < 200ms。
    *   **测试方法**: 模拟大量记忆片段 (例如 10,000 条)，执行多次检索并统计耗时。
*   **混合召回率**: 在包含语义和关键词的测试集上，召回率 > 75%。
    *   **测试方法**: 准备包含不同类型查询的测试集，手动评估检索结果的相关性。
*   **Embedding 成本**: 年度 Embedding API 成本 < $50 (缓存命中率 > 70%)。
    *   **测试方法**: 模拟长时间运行，监控 Embedding API 调用次数和缓存命中率。
*   **存储容量**: 1 年 × 1000 对话的 SQLite 文件 < 1GB。
    *   **测试方法**: 模拟数据写入，检查数据库文件大小。
*   **测试覆盖率**: `memory_manager.py` 模块测试覆盖率 > 80%。
    *   **测试方法**: 编写单元测试和集成测试。

---

## 5. 关键依赖与风险

*   **依赖**:
    *   Phase 3.5 (Transcript Repair) - 已完成
    *   Phase 4 Step 1 (Context Compaction) - 已完成
    *   Phase 4 Step 2 (Memory Flush) - 已完成
    *   `tiktoken` (用于 Token 计数) - 已集成
    *   `fastembed` (用于本地 Embedding)
*   **风险**:
    *   Python 侧 K-NN 检索的性能和稳定性，尤其是在大量数据下的效率。
    *   `FastEmbed` 提供的模型性能是否满足所有场景需求。
    *   混合检索权重 (`α`, `β`) 的调优，可能需要实验。
    *   LLM 在检索结果注入上下文后的理解和利用能力。

---

## 6. 预计文件结构变更

```
closeclaw/
├── context/
│   └── ...
├── memory/
│   ├── __init__.py                  # 修改：导出 MemoryManager
│   ├── memory_flush.py              # 已有
│   └── memory_manager.py            # 新增：SQLite 数据库操作、Embedding、检索逻辑
├── agents/
│   └── core.py                      # 修改：集成 MemoryManager，调用 add_memory 和 retrieve_memory Tool
├── tools/
│   ├── __init__.py
│   └── memory_tools.py              # 新增：定义 retrieve_memory Tool
├── config.py                        # 修改：添加 Embedding 提供商选择和模型路径配置
├── pyproject.toml                   # 修改：添加 `fastembed` 依赖 (无需 `sqlite-vss` 或 `sqlite3` 扩展)
└── tests/
    ├── test_memory_flush.py         # 已有
    └── test_memory_manager.py       # 新增：针对 MemoryManager 的单元测试和集成测试
```

---

## 7. 实施时间表 (参考)

| 周次 | 里程碑 | 交付件 | 验收标准 |
|-----|-------|--------|----------|
| **W1 (3/17-3/23)** | **SQLite 数据库与 Embedding 存储 MVP** | `memory_manager.py` (含 `memory_chunks`, `embedding_cache` 表创建、`add_memory`、`get_embedding` 缓存) | 记忆片段可持久化存储，Embedding 缓存生效 |
| **W2 (3/24-3/30)** | **混合检索与 AgentCore 集成** | `memory_manager.py` (含 `retrieve_memories` 混合检索逻辑)，`core.py` (集成 `MemoryManager`，`retrieve_memory` Tool) | Agent 可主动检索记忆，检索延迟达标 |
| **W3 (3/31-4/06)** | **性能优化与全面测试** | 性能基准测试报告，`test_memory_manager.py` (高覆盖率) | 检索延迟、召回率、成本、存储容量达标，测试通过 |

---

## 8. 批准与后续

**本计划待项目负责人最终签署。一旦签署，即刻启动 Phase 4 Step 3 的开发。**