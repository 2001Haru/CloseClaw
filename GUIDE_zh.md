# CloseClaw GUIDE（详细部署与原理解析）

本文档是 CloseClaw 的深度指南，覆盖部署、架构与运行机制。
`README_zh.md` 用于快速上手；本 GUIDE 用于生产部署、故障定位和高级扩展。

## 1. 仓库完整架构与信息流向

### 1.1 顶层架构

```text
closeclaw/
  agents/          # AgentCore 主循环与任务管理
  channels/        # CLI / Telegram / Feishu / Discord / WhatsApp / QQ 适配层
  cli/             # closeclaw 命令入口与健康检查工具
  compatibility/   # 工具 schema 适配层（legacy/native -> ToolSpecV2）
  context/         # token 计数与上下文压缩基础能力
  cron/            # 定时任务存储与调度服务
  heartbeat/       # 周期唤醒与 HEARTBEAT.md 执行控制
  mcp/             # MCP 客户端、投影、桥接、连接池健康
  memory/          # 工作区记忆布局、记忆压缩、向量记忆数据库
  middleware/      # SafetyGuard -> PathSandbox -> Auth/Guardian
  providers/       # LLM provider 工厂与实现
  sandbox/         # OS 级沙箱执行后端（Windows 受限令牌）
  safety/          # 安全模式、guardian、授权原因构造
  services/        # Prompt/Context/ToolExecution/RuntimeLoop 等服务层
  tools/           # 原生工具（文件/shell/web/cron/spawn）
```

### 1.2 运行时核心职责

- `closeclaw/runner.py`
- 运行时启动编排器。
- 创建 LLM provider、middleware、channel、heartbeat、cron。
- 启动时加载 MCP 并把 MCP 工具投影到运行时。

- `closeclaw/agents/core.py`
- Agent 主循环。
- 维护消息历史、状态恢复/持久化、工具调用生命周期。
- 处理授权等待、授权后恢复、授权中断。

- `closeclaw/services/tool_execution_service.py`
- 原生工具与外部工具统一执行入口。
- 统一归一到 `ToolSpecV2`。
- 执行中间件检查，并在授权重放时强制二次校验。

- `closeclaw/services/context_service.py`
- token 使用分析与阈值处理。
- 记忆压缩流程编排与 compact memory 注入。
- transcript 修复与记忆检索辅助。

- `closeclaw/memory/workspace_layout.py`
- 强制统一记忆目录：`<workspace_root>/CloseClaw Memory`。
- 初始化基础文件并迁移历史散落产物。

### 1.3 compatibility 层（`compatibility/`）

`compatibility` 不是冗余历史代码，而是内部适配边界。

- `ToolSpecV2`：运行时统一工具 schema。
- `NativeAdapter`：把原生工具定义转换到统一 schema。
- `ToolExecutionService` 与 `ToolSchemaService` 依赖此层，从而让 native 工具与 MCP 工具走同一执行路径。

这样既保证内核轻量，也支持多生态工具接入。

### 1.4 持久化产物布局

默认所有运行产物都放在 workspace 下：

```text
<workspace_root>/CloseClaw Memory/
  state.json
  audit.log
  memory.sqlite
  HEARTBEAT.md
  AGENTS.md
  SOUL.md
  USER.md
  TOOLS.md
  SKILLS.md
  memory/
    YYYY-MM-DD.md
```

## 2. 端到端信息处理流向

### 2.1 启动链路

```text
closeclaw agent/gateway
  -> ConfigLoader.load()
  -> 创建主 LLM provider（以及可选 guardian 专属 provider）
  -> 创建 AgentCore
  -> 构建中间件链
       SafetyGuard -> PathSandbox -> AuthPermissionMiddleware
  -> 注册原生工具
  -> 启动 MCP bootstrap 并注册投影工具
  -> 加载历史状态
  -> 启动 heartbeat + cron
  -> 启动启用的 channels
```

### 2.2 单次用户消息处理

```text
Channel 收到消息
  -> AgentCore 组装 prompt
     （system + project context + skills + history + context monitor）
  -> LLM 返回文本与可选 tool_calls
  -> 对每个 tool_call 进入 ToolExecutionService
       -> 中间件检查
       -> 执行工具（native 或 MCP）
  -> 写回 tool results
  -> 循环直到完成
  -> Channel 发送响应（含 token 使用前缀）
```

### 2.3 敏感工具授权流（核心安全路径）

`need_auth` 工具行为由 `safety.security_mode` 决定：

- `autonomous`
- 不走审批，middleware 允许后直接执行。

- `supervised`
- middleware 返回 `requires_auth`。
- runtime 通过 channel 发授权请求并等待用户确认。
- 通过后调用 `execute_authorized_request()` 重放，且强制完整中间件二次校验。

- `consensus`
- middleware 调用 `ConsensusGuardian`（LLM 哨兵）自动审核。
- 通过则自动放行，不需要用户每次点击。
- 拒绝、超时、解析失败、guardian 错误都默认 fail-closed 阻断。

### 2.4 Heartbeat 与 Cron 唤醒闭环

```text
HeartbeatService tick
  -> 读取 <workspace_root>/CloseClaw Memory/HEARTBEAT.md
  -> 决策 run/skip
  -> 将系统唤醒消息入队到 channel queue
  -> AgentCore 按正常消息路径处理该唤醒任务
```

```text
CronService 到点
  -> 将 job 消息入队到 channel queue
  -> AgentCore 按正常消息路径处理
```

唤醒消息并不是特殊后门，仍走完整安全链路。

## 3. Agent Workflow 机制详解

### 3.1 Prompt 组装层级

PromptBuilder 组合顺序：

1. `config.system_prompt` 基础指令。
2. `[PROJECT CONTEXT]`（来自 `AGENTS.md`、`SOUL.md`、`USER.md`、`TOOLS.md`、`SKILLS.md`）。
3. `[WORK INFORMATION]`（UTC/配置时区时间、关键路径）。
4. 技能块（`ALWAYS SKILLS` 与技能索引摘要）。
5. 上下文监控后缀（`当前 token / 最大 token`）。
6. 存在 `retrieve_memory` 时注入 memory recall policy。

### 3.2 记忆压缩与上下文管理

由 `context_management` 控制：

- `WARNING`：触发 memory flush mini-loop。
- `CRITICAL`：触发 deterministic trim，保留最近轮次并注入 compact snapshot。

Memory flush 流程：

```text
命中 WARNING
  -> 注入 flush system prompt
  -> Agent 通过工具读写记忆文件
  -> 输出 [COMPACT_MEMORY_BLOCK]...[/COMPACT_MEMORY_BLOCK]
  -> 输出 [SILENT_REPLY]
  -> runtime 抽取并标准化 compact snapshot
  -> 历史裁剪到 active window
```

### 3.3 安全链路执行顺序

每次工具调用统一经过：

1. `SafetyGuard`
- Shell 模式黑名单（`balanced/strict`）。

2. `PathSandbox`
- 对文件类工具强制路径在 `workspace_root` 内。
- 归一化相对路径。
- 拦截路径穿越与越界写。

3. `AuthPermissionMiddleware`
- 解析 `need_auth` 与当前安全模式。
- 生成授权 reason 与 diff preview。
- consensus 模式下触发 Guardian 审核。

4. OS 级沙箱（仅受保护工具）
- Windows 且命中受保护工具（默认 `shell`）时，使用 restricted token + MIC + JobObject 隔离执行。

### 3.4 OS 级沙箱行为

关键配置：

- `safety.os_sandbox_enabled`
- `safety.os_sandbox_protected_tools`（默认 `['shell']`）
- `safety.os_sandbox_fail_closed`

行为规则：

- 命中受保护工具且后端可用：在受限环境执行。
- 后端失败且 `fail_closed=true`：直接阻断。
- 后端失败且 `fail_closed=false`：回退到普通执行路径。

## 4. 高级配置指南

### 4.1 `llm` 与 guardian 独立模型

基础配置：

```yaml
llm:
  provider: "openai-compatible"
  model: "gpt-4o"
  api_key: "..."
  base_url: "https://api.openai.com/v1"
  temperature: 0.0
  max_tokens: 5000
  timeout_seconds: 60
```

Guardian 独立模型（可选）：

```yaml
safety:
  security_mode: "consensus"
  consensus_guardian_provider: "gemini"
  consensus_guardian_model: "gemini-3-flash"
  consensus_guardian_api_key: "..."      # 可空，缺省回退 llm.api_key
  consensus_guardian_base_url: "..."     # 可空，缺省回退 llm.base_url
  consensus_guardian_timeout_seconds: 20.0
```

回退规则：
- guardian provider/model 未配置或无效时，自动回退主模型。

### 4.2 `safety` 推荐基线

```yaml
safety:
  admin_user_ids: ["cli_user", "YOUR_TELEGRAM_USER_ID"]
  security_mode: "consensus"             # autonomous | supervised | consensus
  default_need_auth: false
  command_blacklist_enabled: true
  command_policy_profile: "balanced"     # balanced | strict
  custom_blacklist_rules: []

  os_sandbox_enabled: true
  os_sandbox_fail_closed: true            # 建议生产开启
  os_sandbox_protected_tools: ["shell"]

  audit_log_enabled: true
  audit_log_path: "CloseClaw Memory/audit.log"
```

### 4.3 `context_management` 调参要点

- `max_tokens`：总上下文预算。
- `warning_threshold`：提前触发 flush 的软阈值。
- `critical_threshold`：触发强制裁剪的硬阈值。
- `active_window`：高保真保留的近期轮数。

长期运行建议更高 `max_tokens` + 保守阈值（例如 0.75/0.95）。

### 4.4 `heartbeat` 与 `cron`

Heartbeat 示例：

```yaml
heartbeat:
  enabled: true
  interval_s: 1800
  quiet_hours:
    enabled: false
    timezone: "UTC+08:00"
    ranges: []
  queue_busy_guard:
    enabled: false
    max_queue_size: 100
  routing:
    target_ttl_s: 1800
    fallback_channel: "cli"
    fallback_chat_id: "direct"
  notify:
    enabled: true
```

Cron 示例：

```yaml
cron:
  enabled: true
  store_file: "CloseClaw Memory/cron_jobs.json"
  default_timezone: "UTC+08:00"
```

说明：
- `cron.store_file` 若为相对路径，会拼到 `workspace_root` 下。

### 4.5 MCP Server 配置与默认策略

```yaml
mcp:
  servers:
    - id: "local-stdio"
      transport: "stdio"
      command: "python"
      args: ["-m", "your_mcp_server"]
      timeout_seconds: 30

    - id: "remote-http"
      transport: "http"
      base_url: "https://example.com"
      endpoint: "/mcp"
      timeout_seconds: 15
      max_retries: 2
      retry_backoff_seconds: 0.2
```

当前 MCP 投影默认行为：

- MCP tool payload 未提供 `need_auth` 时，默认 `need_auth=true`。
- `tool_type` 未知时会尽量做鲁棒归类，最终保守兜底为 `shell`。

### 4.6 Docker 配置注意事项

容器内建议：

- `workspace_root: "/workspace"`
- 挂载 `./workspace` -> `/workspace`
- 挂载 `./runtime-data` -> `/runtime-data`

不要把 Windows 主机绝对路径直接写进容器配置（例如 `D:\...`），容器内是 Linux 路径语义。

## 5. MCP 与 Docker 运维流程

### 5.1 MCP 健康检查

```bash
closeclaw mcp --config config.yaml
closeclaw mcp --config config.yaml --json
```

不健康时优先检查：

- stdio command/args 在同一环境是否可手工运行。
- HTTP `base_url + endpoint` 是否可达。
- runtime 实际读取的是否同一份 `config.yaml`。

### 5.2 Docker 启动推荐顺序

Windows PowerShell：

```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
New-Item -ItemType Directory -Force -Path workspace, runtime-data
```

然后执行：

```bash
docker compose build
docker compose up -d closeclaw-gateway
docker compose logs -f closeclaw-gateway
```

健康检查：

```bash
docker compose exec closeclaw-gateway closeclaw runtime-health --config /app/config.yaml --mode gateway --json
docker compose run --rm closeclaw-cli runtime-health --config /app/config.yaml --mode agent --json
```

期望：
- 返回码 `0`
- JSON 含 `"healthy": true`

## 6. Troubleshootings（故障排查）

### 6.1 配置与启动

- 现象：`workspace_root does not exist`
- 原因：路径无效，或容器内误用了主机路径格式。
- 修复：改为真实存在路径；Docker 内使用 `/workspace`。

- 现象：`No channels enabled for mode=gateway`
- 原因：gateway 模式下只启用了 CLI。
- 修复：至少启用一个非 CLI 通道。

- 现象：`python-telegram-bot is required`
- 原因：telegram 依赖未安装。
- 修复：安装 `closeclaw[telegram]`，或 Docker `INSTALL_EXTRAS=[providers,telegram]` 后重建。

### 6.2 Provider 与授权

- 现象：provider 401
- 原因：API key/base_url/provider-model 组合不匹配。
- 修复：核对 `llm.provider`、`model`、`api_key`、`base_url` 及账户权限。

- 现象：guardian 经常因超时阻断
- 原因：guardian 模型调用超时。
- 修复：提高 `consensus_guardian_timeout_seconds`，并检查 guardian 独立 provider 网络与配置。

### 6.3 区分 Guardian 超时与 OS 沙箱阻断

Guardian 超时属于审核阶段拒绝，常见 reason code 为 `GUARDIAN_TIMEOUT`。

OS 沙箱阻断属于工具执行阶段失败，常见 stderr 类信息：

- `OS sandbox enforcement failed (blocked): ...`（fail-closed 下后端不可用）
- restricted backend 超时或执行失败信息

排查时先分清是“审核拒绝”还是“执行阻断”。

### 6.4 Heartbeat 与 Cron

- 现象：heartbeat 不触发
- 检查：
  - `heartbeat.enabled`
  - `heartbeat.interval_s`
  - `CloseClaw Memory/HEARTBEAT.md` 是否存在且非空
  - quiet-hours 与 queue-busy 是否在抑制触发

- 现象：cron 不执行
- 检查：
  - `cron.enabled`
  - `cron.store_file` 是否可写
  - 时区与表达式是否合法

可用诊断命令：

```bash
closeclaw heartbeat-status --config config.yaml --json
closeclaw heartbeat-trigger --config config.yaml --json
closeclaw cron-list --config config.yaml --json
closeclaw cron-run-now <job_id> --config config.yaml --json
```

## 7. 上线前校验清单

建议上线前逐项确认：

1. `closeclaw runtime-health --config config.yaml --mode gateway --json` 返回 healthy。
2. 目标通道依赖与配置健康。
3. `safety.security_mode` 与 `admin_user_ids` 符合实际策略。
4. `os_sandbox_fail_closed` 已按风险偏好设置。
5. `workspace_root` 下 `CloseClaw Memory` 文件可创建可写。
6. MCP 健康检查通过。
7. heartbeat 与 cron 至少手工触发验证一次。

## 8. 参考文档

- 快速上手（英文）：[README.md](README.md)
- 快速上手（中文）：[README_zh.md](README_zh.md)
- Docker 运维细节：[docs/Docker_Runbook.md](docs/Docker_Runbook.md)
- WhatsApp bridge 协议：[docs/WhatsApp_Bridge_Protocol.md](docs/WhatsApp_Bridge_Protocol.md)

