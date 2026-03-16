# Phase 4 升级计划书：CloseClaw 记忆与上下文管理架构重塑 (参考 Moltbot)

**编制时间**: 2026-03-16
**目标定位**: 将 CloseClaw 从单纯的“执行引擎”升级为具备长线记忆、上下文动态折叠、及极端对话容错能力的“全能旗舰” Agent。

---

## 核心痛点与现状分析 (vs Moltbot)

目前的 CloseClaw (Phase 3 状态) 虽然解决了交互闭环，但在记忆域依然处于“石器时代”：
1. **记忆极速膨胀、硬性截断**：`state.json` 只是一个无限增长的消息数组。一旦触达大模型的 Token 极限（如 128K），只能粗暴丢弃最早的对话。这会引发上下文彻底断裂。
2. **缺乏长线认知（Long-term Memory）**：关闭并重启 Agent 后，虽然任务执行记录还在，但过去的讨论细节完全无法通过关键词或语义被检索。
3. **脆弱的数组对齐要求**：像 Anthropic Claude 等模型对于 `user` -> `assistant(tool_call)` -> `tool_result` 的消息队列有极其严苛的要求。在并发执行或断网重连时，孤儿 Tool Call 极易引发 400 报错使整个进程崩溃。

基于由用户提供的针对 `Moltbot` 记忆架构的神级分析报告，CloseClaw Phase 4 的核心研发将围绕以下四个维度展开：

---

## 核心升级路线图

### 一、 物理记忆引擎：从 JSON 跃迁至 SQLite 混合检索 (Hybrid Search)
**目标**：建立低成本、高效率的持久化 RAG（检索增强生成）知识库。

*   **架构选型**：抛弃笨重的独立向量数据库，引入内嵌的 `sqlite-vec`（或 `lancedb` / `chromadb` 的轻量化本地端），结合 SQLite 原生的 FTS5 全文检索。
*   **实施细则**：
    1.  **双模并发查询**：在 Agent 处理用户请求时，通过内部 Tool 查询历史。同时触达“向量余弦相似度（找概念）”与“FTS 关键词（找特定报错/包名）”两路检索，并使用数学加权合并（如 Moltbot 的 `mergeHybridResults`）。
    2.  **Embedding 缓存**：创建静态表映射文件路径/内容 Hash 到对应的 Vector，彻底消除重启后重复调用 Embedding API 的 Token 消耗。

### 二、 上下文管理与动态“压实” (Context Compaction)
**目标**：解决上下文窗口爆炸问题，保持无限对话的流动性。

*   **架构设计**：不能让 `state.json` 无限膨胀塞给 LLM。
*   **实施细则**：
    1.  **Token 预估与水线监控**：在 `_format_conversation_for_llm` 前置加入 Token 计数器（如 `tiktoken` 粗算）。设定硬性配置如 `MAX_CONTEXT_TOKENS`。
    2.  **三级跳伞压实 (Summarize with Fallback)**：
        *   当水位达到 80%，启动后台 Task 让模型对最旧的 50 轮对话进行 Summarization（总结摘要）。
        *   **降维打击**：如果某条对话自身大到无法被总结（比如几万字的 Log），强制剔除该单条，并用 `[Large payload omitted]` 占位符替代，然后总结剩余部分。
    3.  **无缝拼接**：最终传递给模型的 `messages` 将变成：`[系统盘结的摘要] + [最近 10 轮原始活跃对话]`。

### 三、 记忆冲水前置预警 (Memory Flush Session)
**目标**：在对话历史被压实（变成一句话摘要）之前，诱导 LLM 自动将高价值心血结晶落盘为 Markdown 文件。

*   **架构操作**：Moltbot 最为惊艳的“隐形诱导”机制的复刻。
*   **实施细则**：
    1.  **设置警戒线 (Soft Threshold)**：例如距离满载还有 4000 Token。
    2.  **幽灵指令注入**：当跨越警戒线时，拦截用户的消息请求，先暗中附带一条 System Prompt：
        > *"你接近了自动上下文压实点。请仔细回顾我们这几天的讨论，强制调用 `write_file` 工具将我们得出的任何重要配置、架构决策或代码快照保存到 `workspace/memory/` 下。完成后仅回复 [SILENT_REPLY]。"*
    3.  **无感拦截**：主循环捕获到 `[SILENT_REPLY]` 后，不对用户显示。系统随后清空上下文重置，用户除了感觉略微卡顿一秒外，毫不知情，但核心知识已被永远刻进硬盘（物理记忆）供第一步的 SQLite 检索。

### 四、 极限会话转录修复墙 (Transcript Repair / Guard)
**目标**：无惧断网、人为打断与多线程并发，免疫强对齐模型（如 Claude 3）的 API JSON 格式校验崩溃。

*   **架构设计**：在 `process_message` 真正调用 `llm_provider.generate` 的最后一毫米，设置一道内存清洗门。
*   **实施细则**：
    1.  **孤儿剔除与结果重排**：扫描 `messages` 数组，如果 Tool Call 和 Tool Result 没有绝对的一一对应成对出现，或顺序混乱（被用户并发的新消息插队），代码层强行修正顺序。
    2.  **幻象缝合 (Synthetic Error)**：如果内存中记录了以前发起过某项 Tool Call，但因为重启或强行打断导致永远没收到 Result。大模型接口会当场报错。CloseClaw 需伪造一条假的 ToolResult：`"[System Repair] Tool result missing due to async interrupt. Ignored."` 塞回去。

---

## 阶段性开发优先级 (Phase 4 实施顺序)

根据以上宏大计划，我们应该按以下顺序进行剥离式实现，确保系统始终可运行：

1.  **步骤 1：Transcript Repair 防火墙** (最容易实现，且立刻增强当下通过 Telegram 并发操作的稳定性)。
2.  **步骤 2：Context Compaction 与 Token 计数器** (解决 LLM 窗口爆炸的燃眉之急)。
3.  **步骤 3：Memory Flush 诱导机制** (基于已有的文件读写系统，构建虚拟循环)。
4.  **步骤 4：SQLite + 混合检索基建** (工程量最大，放到最后作为压轴功能模块独立开发)。

> 是否同意此路线图？ 一旦确认，我们将立即从**步骤 1：Transcript Repair 防火墙** 的实现入手。
