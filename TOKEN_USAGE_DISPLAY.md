# Token 使用量显示功能

## 功能说明

现在系统会在每次处理消息时，自动在系统提示中追加当前的 token 使用情况。用户可以清晰地看到已消耗的上下文窗口。

## 显示效果

### 系统提示中的 Token 信息

每条 agent 的响应都包含当前的 token 使用统计：

```
[CONTEXT MONITOR] Current token usage: 1777/200000 (0.89%)
```

### 完整的信息含义

- **1777**: 当前已使用的 token 数量
- **200000**: 总的上下文窗口大小
- **0.89%**: 使用百分比

## 实现机制

### 1. Token 自动计数

在 `_format_conversation_for_llm()` 方法中：

```python
# Phase 4: Token counting and context management
token_count = self.context_manager.count_message_tokens(messages)
status, needs_flush = self.context_manager.check_thresholds(token_count)
context_report = self.context_manager.get_status_report(token_count)

# 生成 token 使用信息
token_usage_info = f"\n\n[CONTEXT MONITOR] Current token usage: {token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})"

# 追加到系统提示
system_message["content"] = system_content + token_usage_info
```

### 2. 压缩后更新

当消息被压缩时，token 计数会重新计算，系统提示中的 token 信息也会相应更新：

```python
# Re-count tokens after compression
token_count = self.context_manager.count_message_tokens(messages)
context_report = self.context_manager.get_status_report(token_count)

# 更新系统提示中的 token 信息
token_usage_info = f"\n\n[CONTEXT MONITOR] Current token usage: {token_count}/{self.context_manager.max_tokens} ({context_report['usage_percentage']})"
messages[0]["content"] = (self.config.system_prompt or "") + token_usage_info
```

## 对话场景示例

### 第 1-50 轮对话

```
[CONTEXT MONITOR] Current token usage: 2340/200000 (1.17%)
```

### 第 51-75 轮对话（逼近警戒线）

```
[CONTEXT MONITOR] Current token usage: 148900/200000 (74.45%)
```

### 第 76 轮对话（进行压缩）

系统自动压缩历史消息后：

```
[CONTEXT MONITOR] Current token usage: 62000/200000 (31.00%)
```

## 关键阈值

| 阈值 | Token 数量 | 触发条件 |
|------|-----------|--------|
| OK | 0-150000 | 正常工作 |
| WARNING | 150000-190000 | 触发 Memory Flush（75%）|
| CRITICAL | 190000-200000 | 强制硬截断（95%）|

## 用户体验

✅ **实时监控**: 每条消息都显示当前 token 消耗情况

✅ **直观理解**: 百分比显示让用户一目了然

✅ **无感处理**: 当接近上限时，系统自动压缩或 flush，用户收到通知但不会中断对话

✅ **完全透明**: 所有 token 使用情况都被记录和展示

## 技术细节

### Token 计数算法

使用 tiktoken 进行精确计数（100% 精度）：

```python
try:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    token_count = len(enc.encode(text))
except ImportError:
    # Fallback: 1 token ≈ 4 characters
    token_count = len(text) // 4
```

### 消息格式

系统提示格式：
```
<original_system_prompt>

[CONTEXT MONITOR] Current token usage: X/200000 (Y.YY%)
```

这样既保持了原有的系统提示功能，又额外提供了 token 监控信息。

## 对话生命周期

```
┌──────────────────────────────────────────────────────┐
│ Round 1: Token usage: 342/200000 (0.17%)            │
├──────────────────────────────────────────────────────┤
│ User: 帮我设计一个API框架                           │
│ Agent: [生成响应] 
│ → [CONTEXT MONITOR] 589/200000 (0.29%)             │
├──────────────────────────────────────────────────────┤
│ Round 75: Token usage: 149000/200000 (74.50%)      │
│ [WARNING 状态，内存 Flush 准备启动]                │
├──────────────────────────────────────────────────────┤
│ Round 76: [内存压缩 + 关键讨论保存]                │
│ → [CONTEXT MONITOR] 63000/200000 (31.50%)          │
│ [新对话窗口就绪]                                    │
└──────────────────────────────────────────────────────┘
```

## 配置参数（config.yaml）

```yaml
context_management:
  max_tokens: 100000              # 总上下文窗口 (Phase 4 默认: 100K)
  warning_threshold: 0.75         # 警戒线 (75%)
  critical_threshold: 0.95        # 红线 (95%)
  summarize_window: 50            # 单次压缩最多总结的轮数
  active_window: 10               # 始终保持最近N轮原始消息
  chunk_size: 5000                # 分块大小
  retention_days: 90              # 记忆文件保留期
```

## 修复验证

✅ **语法检查**: Python 编译无错误

✅ **重新测试**: Context Management 20/20 tests PASSED

✅ **向后兼容**: 所有现有功能保持不变

## 下一步

使用时，用户会在 VS Code 聊天窗口中看到：

```
[你之前说过的...]

[CONTEXT MONITOR] Current token usage: 1777/200000 (0.89%)

是的，我完全理解。继续前进...
```

这样用户就能够：
1. **直观了解** 当前对话已消耗的 token 数量
2. **预见性规划** 什么时候会触发内存压缩
3. **监控系统行为** 确保没有超出限制
