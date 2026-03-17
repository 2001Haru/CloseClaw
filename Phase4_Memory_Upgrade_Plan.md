# Phase 4 升级计划书：CloseClaw 记忆与上下文管理架构重塑 (参考 Moltbot)

**编制时间**: 2026-03-16
**目标定位**: 将 CloseClaw 从单纯的“执行引擎”升级为具备长线记忆、上下文动态折叠、及极端对话容错能力的“全能旗舰” Agent。

---

## 核心痛点与现状分析 (vs Moltbot)

目前的 CloseClaw (Phase 3 状态) 虽然解决了交互闭环，但在记忆域依然处于“石器时代”：
1. **记忆极速膨胀、硬性截断**：`state.json` 只是一个无限增长的消息数组。一旦触达大模型的 Token 极限（如 128K），只能粗暴丢弃最早的对话。这会引发上下文彻底断裂。
2. **缺乏长线认知（Long-term Memory）**：关闭并重启 Agent 后，虽然任务执行记录还在，但过去的讨论细节完全无法通过关键词或语义被检索。
3. **脆弱的数组对齐要求**：像 Anthropic Claude 等模型对于 `user` -> `assistant(tool_call)` -> `tool_result` 的消息队列有极其严苛的要求。在并发执行或断网重连时，孤儿 Tool Call 极易引发 400 报错使整个进程崩溃。

基于由用户提供的针对 `Moltbot` 记忆架构的神级分析报告，CloseClaw Phase 4 的核心研发将围绕以下四个维度展开。

### 技术方案选型与成本分析

#### SQLite 方案的可行性评估

**为什么选 SQLite 而非其他方案？**

| 方案 | 轻量级 | 零运维 | FTS5 | Embedding | 成本 | 推荐度 |
|------|--------|--------|--------|-----------|------|--------|
| **SQLite + tiktoken + 缓存** | ✅ | ✅ | ✅ | 外部 API | $0.02/100K tokens | ⭐⭐⭐⭐⭐ |
| **lancedb** | ✅ | ✅ | ❌ | 灵活 | $0 (开源) | ⭐⭐⭐⭐ |
| **本地 Embedding (sentence-transformers)** | ❌ | ✅ | ❌ | 内置 | ~1GB 内存 | ❌ (与 <50MB 目标冲突) |
| **Pinecone / Weaviate** | ❌ | ❌ | ❌ | 托管 | $$$$ | ❌ (过度设计) |

**结论**：SQLite + 外部 Embedding API（缓存优化）是最优选择。

#### Embedding 缓存策略（防止 API 费用爆炸）

关键设计要点：

```python
# 缓存算法伪代码
def get_embedding(text: str) -> Vector:
    content_hash = sha256(text.encode())
    cached = sqlite_query("cache_table", where=f"hash = {content_hash}")
    
    if cached:
        return cached["embedding"]  # 命中率：~70-80%（重复对话）
    
    # API 调用
    embedding = tiktoken.encode(text)
    sqlite_insert("cache_table", {"hash": content_hash, "embedding": embedding})
    return embedding
```

**预期节省**：
- 第一次运行：全量 Embedding（成本基准）
- 重启体验：缓存命中率 70-80%（省 Token 成本 70-80%）
- 年度估算：假设月均 100K 字符对话 → 缩至 ~$2/月（而非 $20/月）

---

## 核心升级路线图

### 一、 物理记忆引擎：从 JSON 跃迁至 SQLite 混合检索 (Hybrid Search)
**目标**：建立低成本、高效率的持久化 RAG（检索增强生成）知识库。

*   **架构选型**：抛弃笨重的独立向量数据库，引入内嵌的 `sqlite-vec`（或 `lancedb`），结合 SQLite 原生的 FTS5 全文检索。
*   **性能要求**：单次检索 <200ms（否则 Agent 卡顿）
*   **实施细则**：
    1.  **双模并发查询**：在 Agent 处理用户请求时，通过内部 Tool 查询历史。同时触达"向量余弦相似度（找概念）"与"FTS 关键词（找特定报错/包名）"两路检索。
        ```python
        # 伪代码：混合检索权重设计
        α = 0.4   # 语义相似度权重（概念查询）
        β = 0.6   # 关键词权重（错误日志精准匹配）
        threshold = 0.3  # 召回下限（避免垃圾结果）
        
        final_score = α * vector_similarity + β * bm25_score
        if final_score >= threshold:
            return result
        ```
    2.  **Embedding 缓存**：创建静态表映射文件路径/内容 Hash 到对应的 Vector，彻底消除重启后重复调用 Embedding API 的 Token 消耗（详见「技术方案选型」章节）。
    3.  **分库/分表策略**：按时间分片（如月度表 `memory_2026_03`、`memory_2026_04`），防止单表过大。支持多 Agent 实例共享时的行级锁。

### 二、 上下文管理与动态“压实” (Context Compaction)
**目标**：解决上下文窗口爆炸问题，保持无限对话的流动性。

*   **架构设计**：不能让 `state.json` 无限膨胀塞给 LLM。
*   **参数化配置**（添加至 `config.yaml`）：
    ```yaml
    context_management:
      max_tokens: 100000            # Claude 3: 100K, GPT-4: 120K（根据模型调整）
      warning_threshold: 0.75       # 75% 时发出 Soft 警戒线（触发 Memory Flush）
      critical_threshold: 0.95      # 95% 时触发 Hard 截断
      summarize_window: 50          # 一次性总结最多 N 轮对话
      active_window: 10             # 始终保留最近 N 轮原始对话
      chunk_size: 5000              # 总结的 Token 单次上限
      retention_days: 90            # 历史数据保留期限
    ```
*   **实施细则**：
    1.  **Token 预估与水线监控**：在 `_format_conversation_for_llm` 前置加入 Token 计数器（使用 `tiktoken` 精确计数）。
        ```python
        # 伪代码
        token_count = tiktoken.count_tokens(formatted_conversation)
        usage_ratio = token_count / MAX_CONTEXT_TOKENS
        
        if usage_ratio >= 0.75:
            trigger_memory_flush()  # 见第三维度
        if usage_ratio >= 0.95:
            force_truncate()        # 强制截断
        ```
    2.  **三级跳伞压实 (Summarize with Fallback)**：
        *   当水位达到 75%（Soft 警戒线），启动后台 Task 让模型对最旧的 `SUMMARIZE_WINDOW`（默认 50）轮对话进行 Summarization（总结摘要）。
        *   **降维打击**：如果某条对话自身大到无法被总结（比如几万字的 Log），强制剔除该单条，并用 `[Large payload omitted]` 占位符替代，然后总结剩余部分。
        *   当水位达到 95%（Hard 截断），立即舍弃最早的消息（不再等待总结完成）。
    3.  **无缝拼接**：最终传递给模型的 `messages` 将变成：`[系统总结的摘要] + [最近 N 轮原始活跃对话]`，其中 N = `ACTIVE_WINDOW`。

### 三、 记忆冲水前置预警 (Memory Flush Session)
**目标**：在对话历史被压实（变成一句话摘要）之前，诱导 LLM 自动将高价值心血结晶落盘为 Markdown 文件。

*   **架构操作**：Moltbot 最为惊艳的"隐形诱导"机制的复刻，但需平衡 UX 与透明度。
*   **伦理审视**：
    - **问题**："幽灵指令"属于隐形操纵，用户无法审查自动写入的数据
    - **与 CloseClaw 安全哲学的关系**：Zone C 强制 HITL 确认的初心是"让用户掌控"，而诱导写文件违背了这一原则
    - **推荐方案**：诱导 + 事后通知（平衡自动化与透明度）
*   **实施细则**：
    1.  **设置警戒线 (Soft Threshold)**：距离满载还有 4000 Token（配置 `warning_threshold: 0.75`）。
    2.  **幽灵指令注入与后续通知**：当跨越警戒线时，拦截用户的消息请求，先暗中附带一条 System Prompt：
        > *"你接近了自动上下文压实点。请仔细回顾我们这几天的讨论，强制调用 `write_file` 工具将我们得出的任何重要配置、架构决策或代码快照保存到 `workspace/memory/` 下。完成后仅回复 [SILENT_REPLY]。"*
    3.  **无感拦截 + 事后透明化**：主循环捕获到 `[SILENT_REPLY]` 后：
        - 不对用户显示本次 flush 过程（保留顺滑 UX）
        - 系统随后清空上下文重置
        - **关键**：发送事后通知给用户：
        ```
        ✅ [System] 自动保存了论讨总结到 workspace/memory/session_2026-03-16_14h.md
        📝 内容预览：[前 200 字...]
        🔗 查看完整内容：review_memory session_2026-03-16_14h
        ```
        这样用户虽然体感卡顿一秒，但事后完全可以审查或删除自动保存的文件。
    4.  **强化 Audit 可审计性**：在 `audit.log` 中记录：
        ```
        [2026-03-16 14:30:45] MEMORY_FLUSH_TRIGGERED context_ratio=0.78 saved_to=session_2026-03-16_14h.md
        ```

### 四、 极限会话转录修复墙 (Transcript Repair / Guard) ⚡ **Phase 3.5 紧急补丁**

**优先级重新定位**：原计划中此项为"步骤1"，现提升为 **Phase 3.5 紧急补丁**。

**提升理由**：
- 这是数据质量基础，其他三项都依赖它
- Phase 3 已知存在"孤儿 Tool Call"问题（用户打断时）
- Claude API 对消息格式极为敏感，缺陷会导致整个进程崩溃
- 越早修复成本越低

**目标**：无惧断网、人为打断与多线程并发，免疫强对齐模型（如 Claude 3）的 API JSON 格式校验崩溃。

*   **架构设计**：在 `process_message` 真正调用 `llm_provider.generate` 的最后一毫米，设置一道内存清洗门。该防护应该是**同步、轻量、零成本**的（不调用 LLM）。
*   **实施细则**：
    1.  **孤儿剔除与结果重排**：
        ```python
        def _repair_transcript(messages: List[Message]) -> List[Message]:
            """
            1. 扫描孤儿 Tool Call（有 Call 但无对应 Result）
            2. 扫描悬空 Result（有 Result 但无对应 Call）
            3. 检测消息顺序混乱（user -> result -> call）→ 强制重排
            4. 去除重复 Call ID（并发时可能重试多次）
            """
            pass
        ```
    2.  **幻象缝合 (Synthetic Completion)**：如果内存中记录了以前发起过某项 Tool Call，但因为重启或强行打断导致永远没收到 Result。大模型接口会当场报错。CloseClaw 需伪造一条假的 ToolResult：
        ```python
        ToolResult(
            tool_call_id=orphan_call.id,
            content="[System Repair] Tool result missing due to async interrupt. Ignored.",
            is_error=True
        )
        ```
        这样 Claude 就看到了"成对的"Call+Result，不会报格式错误。
    3.  **防守性编程**：添加重试上限与日志记录。每次修复都在 `audit.log` 中记录：
        ```
        [TRANSCRIPT_REPAIR] orphan_call_removed=3 result_reordered=1 synthetic_result_added=2
        ```

---

## 阶段性开发优先级 (Phase 4 实施顺序)

根据以上宏大计划，我们应该按以下顺序进行剥离式实现，确保系统始终可运行：

### 首先：Phase 3.5 紧急补丁（在 Phase 4 开始前完成）

0.  **步骤 0：Transcript Repair 防火墙** ⚡ **立即执行，不依赖其他模块**
    - 实现：轻量级的消息数组清洗逻辑（<100 LOC）
    - 放置：`closeclaw/agents/core.py` 的 `_format_conversation_for_llm` 前置
    - 收益：立刻增强当下通过 Telegram 并发操作的稳定性，防止 Claude API 400 错误
    - 预期缺陷修复率：>90%（拦截 Tool Call/Result 错位导致的崩溃）

### 然后：Phase 4 主线（分阶段递进）

1.  **步骤 1：Context Compaction 与 Token 计数器** 
    - 依赖：Phase 3.5（Transcript Repair）
    - 工作量：中等（参数化配置 + tiktoken 集成）
    - 收益：解决 LLM 窗口爆炸的燃眉之急
    - 检验：当处理 100 轮对话时，Token 计数精度 >98%

2.  **步骤 2：Memory Flush 诱导机制** 
    - 依赖：步骤 1（已知何时触发 Flush）
    - 工作量：中等（幽灵指令注入 + 事后通知）
    - 收益：自动保存高价值讨论，防止上下文截断丢失信息
    - 关键：完美平衡诱导与透明度（事后通知 + 可审计）

3.  **步骤 3：SQLite + 混合检索基建** 
    - 依赖：步骤 1、2（已有压实后的摘要和保存的 Markdown 文件）
    - 工作量：最大（向量化 + 索引 + 查询优化）
    - 收益：真正的长线记忆与无限对话容量
    - 检验：单次检索 <200ms，混合召回率 >75%

> **关键阶段依赖关系**：
> ```
> Phase 3.5 (Transcript Repair)
>     ↓
> Phase 4 Step 1 (Context Compaction)
>     ↓
> Phase 4 Step 2 (Memory Flush)
>     ↓
> Phase 4 Step 3 (SQLite RAG)
> ```
> 务必严格按此顺序。一旦 Phase 3.5 完成，即可立即启动 Phase 4 Step 1。

---

## 完整性检查清单

**以下项目在实施各步骤时务必纳入考虑**：

| 项目 | 当前状态 | 责任步骤 | 检验标准 |
|------|---------|---------|----------|
| **Embedding 模型选择** | 未明确 | 步骤 3 | 确定使用 OpenAI `text-embedding-3-small` 或开源替代方案 |
| **Embedding 成本预算** | 未定量 | 步骤 3 | 年度成本 <$50（缓存命中率 >70%） |
| **向量检索延迟** | 未测试 | 步骤 3 | 单次查询 <200ms（p99 <500ms） |
| **混合检索权重参数** | 已定义 α/β | 步骤 3 | 实现中可调，初值 α=0.4 β=0.6 threshold=0.3 |
| **存储容量规划** | 未提及 | 步骤 3 | 1 年 × 1000 对话的 SQLite 文件 <1GB |
| **回滚/恢复机制** | 未设计 | 步骤 1-2 | 压实后可恢复原始消息（保留备份 30 天） |
| **多用户隔离** | 未提及 | 步骤 3 | 多个 Agent 实例共享 SQLite 时的行级锁机制 |
| **断线重连恢复** | 已有基础 | 步骤 0 | Transcript Repair 修复 >90% 的孤儿 Call |
| **测试覆盖率** | 未定量 | 全步骤 | 每步完成后新增测试，整体 >80% 覆盖 |
| **文档完善** | 待定 | 步骤 3 后 | 补充"长线记忆"和"Context Compaction"用户指南 |

---

## 附录：实施里程碑时间表

**假设每步骤 1 周工作量的参考时间表**：

| 周次 | 里程碑 | 交付件 | 验收标准 |
|-----|-------|--------|----------|
| **W1 (3/16-3/22)** | Phase 3.5: Transcript Repair 完成 | `closeclaw/agents/core.py` 防护逻辑 | 修复率 >90%，无新增 Claude 400 错误 |
| **W2 (3/23-3/29)** | Phase 4 Step 1: Context Compaction | `config.yaml` 参数化 + tiktoken 集成 | Token 计数精度 >98%，压实流程可验证 |
| **W3 (3/30-4/05)** | Phase 4 Step 2: Memory Flush | 幽灵指令 + 事后通知完整 | 自动保存成功率 >95%，用户可审查 |
| **W4 (4/06-4/12)** | Phase 4 Step 3: SQLite 基建 MVP | 向量化 + FTS5 索引 + 混合查询 | 单次检索 <200ms，基础测试通过 |
| **W5 (4/13-4/19)** | 性能优化 & 文档完善 | 性能基准测试 + 用户指南 | 整体测试 >80% 通过，交付生产版本 |

> **灵活调整**：此时间表为参考，具体进度可根据实际情况调整。关键是保证各步之间的依赖关系不被破坏。

---

## 批准与后续

**本计划由以下共同确认**：
- ✅ AI 助手审核并调整（2026-03-16）
- ⏳ 待项目负责人最终签署

**一旦签署，即刻启动 Phase 3.5 (Transcript Repair 防火墙)。**
