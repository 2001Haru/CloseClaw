# Agent Prompt 构建全流程分析报告

**生成时间**: 2026-03-18  
**分析对象**: CloseClaw Agent (Phase 4)

---

## 1. Prompt 构建的完整组成部分

### 1.1 基础层（Base Layer）

#### 系统提示（System Prompt）
```
source: self.config.system_prompt
位置: _format_conversation_for_llm() 行 627-634
作用: 设定 Agent 的角色、行为准则、核心能力
特点: 用户可配置，通常包含 Agent 身份和基本指导
```

#### Token 使用信息提示（Token Usage Info）
```
source: ContextManager.get_status_report()
位置: _format_conversation_for_llm() 行 672-705
作用: 实时显示当前 token 消耗比例
内容格式: "[CONTEXT MONITOR] Current token usage: {token_count}/{max_tokens} ({usage_percentage})"
特点: 动态更新，帮助 Agent 理解上下文压力
```

### 1.2 消息层（Message Layer）

#### 对话历史（Conversation History）
```
source: self.message_history (list[Message])
位置: _format_conversation_for_llm() 行 637-659
组成:
  - 用户消息: role="user"
  - Agent 响应: role="assistant"
  - 工具调用: msg_dict["tool_calls"] (OpenAI 格式)
  - 工具结果: role="tool" 消息
```

#### 工具结果消息（Tool Result Messages）
```
位置: _format_conversation_for_llm() 行 661-680
状态处理:
  - success: 返回 JSON 格式的执行结果
  - auth_required: "Operation requires user authorization..."
  - error/blocked: "Error or Blocked ({status}): {error}"
截断规则: MAX_RESULT_CHARS = 10000 字符
```

### 1.3 上下文管理层（Context Management Layer）

#### Token 计数与压力检测
```
位置: _format_conversation_for_llm() 行 684-705
步骤:
  1. 计算消息总 token 数: context_manager.count_message_tokens(messages)
  2. 检查阈值: context_manager.check_thresholds(token_count)
  3. 返回状态: "OK" | "WARNING" | "CRITICAL"
  4. 标记冲水需求: memory_flush_coordinator.mark_flush_pending()

阈值配置（来自 ContextManagementSettings）:
  - warning_threshold: 0.75 (75%)
  - critical_threshold: 0.95 (95%)
```

#### 消息压缩（Message Compaction）
```
位置: _format_conversation_for_llm() 行 707-718
触发条件: 状态 == "CRITICAL" 或 usage_ratio >= 0.80
压缩策略: MessageCompactor.apply_compression_strategy()
  - 保留最新 active_window 消息
  - 对早期消息进行摘要
  - 重新计算 token 数
历史修剪: 
  - 目标大小 = max(active_window * 2, 5)
  - 保留最新 N 条消息
```

#### 冲水诱导（Memory Flush Induction）
```
位置: _format_conversation_for_llm() 行 675-681
检测: memory_flush_coordinator.mark_flush_pending(status, usage_ratio)
注入: 将冲水系统提示注入消息列表
标记: self.pending_flush_before_next_message = True
特点: 隐形执行，用户不可见
```

### 1.4 工具层（Tools Layer）

#### 工具列表格式化
```
位置: _format_tools_for_llm() 行 1070-1132
输出格式 (OpenAI 标准):
{
  "type": "function",
  "function": {
    "name": "tool_name",
    "description": "tool_description",
    "parameters": {
      "type": "object",
      "properties": {
        "param1": {"type": "string", "description": "..."},
        ...
      },
      "required": ["param1", ...]
    }
  }
}

参数兼容性处理:
  - JSON Schema 格式: {"type": "object", "properties": {...}, "required": [...]}
  - Legacy dict 格式: {"param": {"type": "string", "description": "..."}}
  - 简写 string 格式: {"param": "string"}
```

#### 内置工具：retrieve_memory
```
位置: AgentCore.__init__() 行 112-128
功能: 允许 Agent 查询长期记忆数据库
参数: query (string) - 搜索查询文本
返回: 格式化的相关记忆列表，包含：
  - 内容摘要
  - 相关性评分
  - 来源标记
  - 时间戳
```

---

## 2. Prompt 构建的完整流程（时间顺序）

### Phase 0: 预检查（Pre-flight Check）

```
时间: process_message() 行 206-246
步骤:
  1. 检查当前历史 token 数
     → 不含新消息的 baseline
  2. 预测添加新消息后的 token 数
     → with_new_message_tokens
  3. 评估阈值状态
     → 决定是否需要冲水
  4. 日志输出压力指标
     → 供用户和系统调试
```

### Phase 1: 决策（Decision）

```
时间: process_message() 行 249-262
条件:
  IF new_ratio >= 0.80 OR needs_flush == "CRITICAL":
    执行冲水 (Memory Flush)
  ELSE:
    继续正常处理

冲水路径 (if triggered):
  1. 调用 _execute_memory_flush_with_context(message_history)
  2. 向 LLM 发送完整历史 + 冲水指令
  3. LLM 提取关键信息并写入文件
  4. 自动索引到 SQLite 向量库
  5. 清空历史，重新开始
```

### Phase 2: 消息格式化（Message Formatting）

```
时间: process_message() 行 268 或 333
函数: _format_conversation_for_llm()
流程:
  1. 收集系统提示 (system_prompt)
  2. 遍历 message_history，转换为 LLM 格式
     - 标记 sender 角色 (user/assistant)
     - 格式化工具调用 (OpenAI function calling format)
     - 添加工具结果消息
  3. 计数 token
  4. 检查压力阈值
  5. 可能触发消息压缩
  6. 返回 LLM 就绪的消息列表
```

### Phase 3: 工具列表准备（Tools Preparation）

```
时间: process_message() 行 269 或 334
函数: _format_tools_for_llm()
流程:
  1. 遍历 self.tools (注册的所有工具)
  2. 对每个工具进行 OpenAI 标准格式化
  3. 处理参数定义的多种形式
  4. 返回 LLM 就绪的工具定义列表
```

### Phase 4: LLM 调用（LLM Generation）

```
时间: process_message() 行 272-277 或 339-343
调用:
  response_text, tool_calls = await llm_provider.generate(
    messages=messages_for_llm,
    tools=tools_for_llm,
    temperature=self.config.temperature
  )

传入信息:
  - messages_for_llm: 格式化的对话 + 系统提示 + token 使用信息
  - tools_for_llm: OpenAI 格式的工具定义
  - temperature: 创意度参数
```

### Phase 5: 工具执行与授权检查（Tool Execution & Auth）

```
时间: process_message() 行 290-298
流程:
  FOR EACH tool_call in tool_calls:
    1. 通过中间件链检查权限:
       - SafetyGuard: 检查危险命令
       - PathSandbox: 检查文件路径
       - ZoneBasedPermission: 检查授权需求
    2. IF requires_auth:
       → 生成授权请求，等待用户批准
    3. ELSE:
       → 执行工具，获取结果
    4. 将结果添加到 tool_results 列表
```

### Phase 6: 历史保存（History Persistence）

```
时间: process_message() 行 302-310 或 393-410
保存内容:
  新 Message 对象包含:
    - id: 唯一标识符
    - sender_id: agent_id 或 user_id
    - content: LLM 响应文本
    - tool_calls: 执行的工具调用
    - tool_results: 工具执行结果
    - timestamp: 消息时间

存储位置: self.message_history (内存列表)
后续持久化: 通过 state.json 在 _save_state()
```

---

## 3. 核心配置参数汇总

| 参数 | 位置 | 默认值 | 作用 |
|------|------|--------|------|
| system_prompt | AgentConfig.system_prompt | None | 系统级角色定义 |
| max_tokens | ContextManagementSettings.max_tokens | 100000 | Token 限制 |
| warning_threshold | ContextManagementSettings | 0.75 | 冲水触发点 |
| critical_threshold | ContextManagementSettings | 0.95 | 压缩触发点 |
| summarize_window | ContextManagementSettings | 50 | 摘要消息窗口 |
| active_window | ContextManagementSettings | 10 | 保活消息窗口 |
| temperature | AgentConfig.temperature | 0.0 | LLM 创意度 |
| MAX_RESULT_CHARS | _format_conversation_for_llm | 10000 | 工具结果截断 |

---

## 4. 重复总结问题根源分析

### 问题现象
```
观察到多个 memory_*.md 文件内容相同或极相似:
  - memory_f1_overview.md
  - memory_fe_overview.md
  - memory_FormulaE_overview.md
```

### 根本原因

**1. 冲水逻辑缺陷**
```
当前行为:
  - Agent 在 WARNING 状态 (75%) 触发冲水
  - 注入"写入记忆"系统提示
  - LLM 调用 write_memory_file 工具
  - 每次冲水都生成新文件

问题:
  - 没有检查"该信息是否已被记忆"
  - 没有去重机制
  - 没有版本控制
  - 历史被清空后，LLM 无法感知之前写过什么
```

**2. 记忆检索不畅**
```
理想行为:
  - Agent 调用 retrieve_memory("Formula 1") 查询现有记忆
  - 发现已存在相关记忆
  - 避免重复写入

实际行为:
  - retrieve_memory 工具可用，但 LLM 未主动调用
  - 冲水诱导仅让 LLM "写"，不让 LLM "查"
  - 导致盲目写入
```

**3. 冲水系统提示不完整**
```
当前 flush_prompt 缺少:
  - "检查现有记忆，避免重复" 的指导
  - 记忆检索建议
  - 去重策略指导
```

---

## 5. 改进建议

### 5.1 短期修复

**增强冲水提示**
```python
# 在 MemoryFlushSession.create_flush_system_prompt() 中添加:

修改前:
  "请使用 write_memory_file 工具保存关键信息"

修改后:
  "1. 首先调用 retrieve_memory 检查是否已有相关记忆
   2. 如果已有相似记忆，则合并或增强，而不是创建新文件
   3. 只在信息完全新颖时创建新文件
   4. 文件名应反映内容的唯一特征"
```

### 5.2 中期优化

**实现智能去重**
```
在 MemoryManager 层添加:
  - 重复检测: 使用向量相似度 (>0.9) 作为重复判定
  - 版本控制: 记录文件修改历史
  - 合并策略: 自动合并相似记忆
```

### 5.3 长期架构

**记忆生命周期管理**
```
阶段 1 (First Memory):
  - 创建新记忆文件，索引到 SQLite

阶段 2 (Update Request):
  - 检测重复，提示用户/Agent 选择合并
  - 更新现有记忆而不是创建新文件

阶段 3 (Consolidation):
  - 定期合并高相似度记忆
  - 保持记忆库的整洁和高效
```

---

## 总结

Agent Prompt 构建是一个**多层次、多阶段的动态过程**，涉及：
1. **静态层**: 系统提示 + 工具定义
2. **动态层**: 对话历史 + token 使用信息
3. **管理层**: 压力检测、冲水诱导、消息压缩
4. **执行层**: 权限检查、工具调用、结果反馈

当前"多次重复总结"问题的根源在于**冲水流程缺少记忆查询和去重指导**，导致 Agent 在无法感知历史的情况下盲目写入新文件。

建议下一步优先解决冲水提示的完整性，以减少重复记忆的生成。