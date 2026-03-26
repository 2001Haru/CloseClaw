<p align="center">
  <img src="assets/logo.png" alt="CloseClaw Logo" width="1200">
</p>

[EN](README.md) | [中文](README_zh.md)

<p align="center">
  <h1>轻量 • 安全优先 • 强大 Agent</h1>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/Runtime-Stable-1f883d">
  <img alt="Channels" src="https://img.shields.io/badge/Channels-CLI%20%7C%20Telegram%20%7C%20Feishu%20%7C%20Discord%20%7C%20WhatsApp%20%7C%20QQ-0a7ea4">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-f2cc60">
</p>

# 版本 1.12.0. 欢迎贡献一份力!

> 🔥 **CloseClaw** 是一个轻量、安全优先且实用的 OpenClaw 风格 Python 框架。它可作为本地与多通道自动化的个人 Agent，内置安全护栏、任务调度与记忆能力。

---

# ✨ 为什么选择 CloseClaw

- ### ⚡ **部署快**
- 轻量、易部署，带有受保护的编排循环，兼顾性能与稳定。个人强大 AI Agent，分钟级启动。

- ### 🧠 **记忆管理 + 主动执行**
- 完整复刻 OpenClaw 记忆管理系统，配合 Heartbeat 与 Cron 支持计划性/主动性执行。长期记忆可持续，认知行为可定制。

- ### 🛡️ **安全优先**
- 清晰严格的沙箱权限体系，包含哨卫 Agent 审核行为机制与命令黑名单约束，降低高风险误操作。

- ### 🔌 **MCP 可扩展**
- 原生支持 MCP 扩展，兼容 OpenClaw 生态技能与工具，便于快速武装 Agent 能力。

---

## 🎯 运行模式

| 模式 | 实际运行内容 | 典型用途 |
|---|---|---|
| `agent` | 仅 CLI | 本地交互调试 / 开发 |
| `gateway` | 仅非 CLI 通道 | Bot 网关部署 |
| `all` | CLI + 所有启用通道 | 本地全链路联调 |

---

## 📡 通道支持

### 已支持通道
- `cli`
- `telegram`
- `feishu`
- `discord`
- `whatsapp`（bridge）
- `qq`

### 启动时通道提示
- Feishu：打印 webhook 地址（host/port）
- WhatsApp：打印 bridge URL
- Telegram / Discord / QQ：打印 gateway/polling 启动提示

---

## 🤖 LLM Provider 支持

- `openai` / `openai-compatible`（默认友好）
- `gemini`（通过 LiteLLM runtime）
- `anthropic`（通过 LiteLLM runtime）
- `ollama`（本地开发独立 provider runtime）

---

## 🚀 快速开始

### 1) 安装

```bash
git clone https://github.com/closeclaw/closeclaw.git
cd closeclaw
pip install -e .
```

可选依赖：

```bash
pip install -e ".[telegram]"
pip install -e ".[discord]"
pip install -e ".[whatsapp]"
pip install -e ".[qq]"
pip install -e ".[fastapi]"
pip install -e ".[providers]"
```

> 📝 `.[providers]` 会安装 `litellm`，是 `gemini` 和 `anthropic` provider 所需依赖。

### 2) 生成配置

```bash
cp config.example.yaml config.yaml
```

最小配置示例：

```yaml
agent_id: "closeclaw-main"
workspace_root: "your/workspace"

llm:
  provider: "openai-compatible"
  model: "gpt-4"
  api_key: "sk-..."
  base_url: "https://api.openai.com/v1"

channels:
  - type: "cli"
    enabled: true

safety:
  admin_user_ids: ["cli_user"]
  default_need_auth: false
```

### 3) 运行

Agent 模式（仅 CLI）：

```bash
closeclaw agent --config config.yaml
```

Gateway 模式：

```bash
closeclaw gateway --config config.yaml
```

✨这就是你的私人 Agent！

---

## 🐳 Docker（可选）

Docker 支持是可选路径，不影响原生 Windows/Linux 使用体验。在配置 Docker 之前，确保你已经启动了你的 Docker Engine，并且确保你配置了合适的 Docker 代理以避免网络问题。

### 1) 准备宿主机文件

```bash
cp .env.example .env
cp config.example.yaml config.yaml
mkdir -p workspace runtime-data
```

### 2) 构建镜像

```bash
docker build -t closeclaw:local .
```

构建时安装可选依赖：

```bash
docker build --build-arg INSTALL_EXTRAS="[providers,telegram,discord,whatsapp,qq]" -t closeclaw:local .
```

### 3) 使用 docker run

Agent 模式：

```bash
docker run --rm -it \
  -v ${PWD}/config.yaml:/app/config.yaml:ro \
  -v ${PWD}/workspace:/workspace \
  -v ${PWD}/runtime-data:/runtime-data \
  closeclaw:local agent --config /app/config.yaml
```

Gateway 模式：

```bash
docker run -d --name closeclaw-gateway \
  -v ${PWD}/config.yaml:/app/config.yaml:ro \
  -v ${PWD}/workspace:/workspace \
  -v ${PWD}/runtime-data:/runtime-data \
  -p 9000:9000 \
  closeclaw:local gateway --config /app/config.yaml
```

### 4) 使用 docker compose

```bash
docker compose build
docker compose up -d closeclaw-gateway
docker compose logs -f closeclaw-gateway
docker compose run --rm closeclaw-cli agent --config /app/config.yaml
docker compose down
```

### 5) 持久化与密钥管理

- `./workspace` 映射到 `/workspace`，用于你的实际工作目录。
- `./runtime-data` 用于保存运行时状态/记忆，容器重启后不丢失。
- `./config.yaml` 以只读方式挂载到 `/app/config.yaml`。
- 密钥建议放在 `.env`，并在配置中通过 `${ENV_VAR}` 引用。
- 容器内 shell/file 工具运行在容器命名空间中，需要访问宿主文件时请通过 bind mount 显式挂载。

### 6) Docker 常见问题（路径/权限）

- 运行时目录写入权限不足：
  - 启动前先创建 `workspace`、`runtime-data` 目录。
  - 检查宿主机目录所有者与写权限。
- 工作区路径不符合预期：
  - compose 环境中保持 `WORKSPACE_ROOT: /workspace`。
  - 配置内 `workspace_root` 与挂载路径策略保持一致。
- healthcheck 长期 unhealthy：
  - 检查 `config.yaml` 是否存在且可解析。
  - 检查 `INSTALL_EXTRAS` 是否包含所需 provider/channel 依赖。

生产化建议与硬化细节见 [docs/Docker_Runbook.md](docs/Docker_Runbook.md)。

---

## 🧱 架构总览

```text
closeclaw/
├─ runner.py                              # 运行时入口 + channel/heartbeat/cron 编排
├─ agents/
│  └─ core.py                             # AgentCore：主编排循环与执行生命周期
├─ services/
│  ├─ tool_execution_service.py           # 工具路由 + 中间件 + 授权处理
│  └─ context_service.py                  # 上下文整形、压缩与窗口管理
├─ memory/
│  └─ memory_manager.py                   # 记忆检索与持久化协调
├─ channels/                              # CLI / Telegram / Feishu / Discord / WhatsApp / QQ 适配器
├─ tools/                                 # 文件 / Shell / Web / 调度 / 记忆辅助工具
└─ mcp/                                   # MCP 传输层、连接池、桥接与健康检查
```

### 核心职责
- `runner`：启动编排（channels、heartbeat、cron、agent 生命周期）
- `AgentCore`：主编排循环 + 工具决策与执行流
- `ToolExecutionService`：工具路由、中间件、授权交互
- `ContextService`：上下文整形与压缩策略
- `MemoryManager`：记忆检索与持久化层

### 主要模块
- `closeclaw/runner.py`
- `closeclaw/agents/core.py`
- `closeclaw/services/tool_execution_service.py`
- `closeclaw/services/context_service.py`
- `closeclaw/memory/memory_manager.py`

---

## 🔐 安全模型

CloseClaw 采用分层安全控制：

1. ✅ **人工授权**
   - 标记为 `need_auth=True` 的工具需要显式审批。
2. ✅ **工作区沙箱**
   - 文件路径会标准化并限制在 `workspace_root` 内。
3. ✅ **命令黑名单**
   - 高风险 shell 模式在执行前被拦截。
4. ✅ **审计日志**
   - 运行行为记录到 `safety.audit_log_path`。

---

## 🧰 内置工具

工具分组：
- 📁 文件工具：read/write/edit/delete/list/exists/size/line-range
- 🖥️ Shell 工具：shell 执行 + pwd
- 🌍 Web 工具：web_search + fetch_url
- ⏲️ 调度工具：call_cron
- 🧠 记忆工具：write/edit memory file

Web 搜索行为：
- 当前 provider 为 Brave Search API
- 默认关闭，需配置 `web_search.enabled=true` 及 API key

---

## ⚙️ 配置参考

### `llm`
关键字段：`provider`、`model`、`api_key`、`base_url`、`temperature`、`max_tokens`、`timeout_seconds`

Gemini 示例：

```yaml
llm:
  provider: "gemini"
  model: "gemini-2.5-flash"
  api_key: "YOUR_GEMINI_API_KEY"
  temperature: 0.2
  max_tokens: 4096
```

Anthropic 示例：

```yaml
llm:
  provider: "anthropic"
  model: "claude-3-7-sonnet"
  api_key: "YOUR_ANTHROPIC_API_KEY"
  temperature: 0.2
  max_tokens: 4096
```

Ollama 本地示例：

```yaml
llm:
  provider: "ollama"
  model: "llama3.1"
  api_key: ""
  base_url: "http://localhost:11434"
  temperature: 0.0
  max_tokens: 4096
```

### `web_search`

```yaml
web_search:
  enabled: false
  provider: "brave"
  brave_api_key: "BSA-..."
  timeout_seconds: 30
```

### 其他高影响配置块
- `safety`：管理员、默认授权、黑名单、审计配置
- `context_management`：token 窗口、阈值、保留策略
- `orchestrator`：步数、墙钟时长、无进展阈值
- `heartbeat`：间隔、静默时段、队列保护、路由
- `cron`：存储路径、时区、启停控制

### `Memory and Soul`（工作区个性化）

首次运行后，进入工作区本地目录：

```text
<workspace_root>/CloseClaw Memory/
```

可通过编辑以下文件个性化运行时行为：
- `AGENTS.md`：Agent 策略/人格与协作偏好
- `SOUL.md`：长期身份、语气与行为风格
- `USER.md`：用户偏好与约束
- `TOOLS.md`：工具使用约定与边界
- `SKILLS.md`：技能级指导与触发约定
- `HEARTBEAT.md`：周期性主动行为指令
- `memory/YYYY-MM-DD.md`：日级记忆笔记与上下文快照

建议：
- 规则尽量简洁、明确、避免冲突。
- 稳定人格规则放 `SOUL.md`，任务临时指导放每日 `memory/`。
- 行为漂移时，先检查 `AGENTS.md` + `SOUL.md`，再清理冲突笔记。

---

## 📦 MCP 配置教程

CloseClaw 当前支持：
- MCP server 健康诊断
- 通过 `MCPBridge` 投影 MCP 工具

### 第 0 步：规划服务目录

```text
<repo-root>/
  mcp_servers/
    weather_server/
    docs_server/
```

### 第 1 步：准备服务
- 仓库内 Python MCP 服务，或
- 使用 `npx` / `npx.cmd` 运行 npm 托管服务

### 第 2 步：配置 `config.yaml`

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

### 第 3 步：健康检查

```bash
closeclaw mcp --config config.yaml
closeclaw mcp --config config.yaml --json
```

### 第 4 步：启动运行时

```bash
closeclaw agent --config config.yaml
```

> ✅ Runner 会自动加载 MCP 服务并将工具 schema 同步到运行时。

### 第 5 步：快速排错
- stdio 不健康：手工验证 command 与 args
- http 不健康：检查 base_url + endpoint 可达性
- 配置未生效：确认实际传入的 `--config` 文件一致
- 工具未被选中：检查 bootstrap 流程与工具名冲突

---

## 🧠 Memory 目录结构

```text
<workspace_root>/
  CloseClaw Memory/
    state.json
    audit.log
    memory.sqlite
    HEARTBEAT.md
    MEMORY.md
    AGENTS.md
    SOUL.md
    USER.md
    TOOLS.md
    SKILLS.md
    memory/
      YYYY-MM-DD.md
```

采用该布局的原因：
- 运行产物与源码目录解耦
- 备份与迁移更简单
- 支持从历史分散路径平滑升级

---

## 🧪 测试

聚焦测试：

```bash
python -m pytest tests/test_config.py -q
python -m pytest tests/test_tools.py tests/test_tool_execution_service.py -q
python -m pytest tests/test_runner.py tests/test_heartbeat_service.py tests/test_cron_service.py -q
```

全量测试：

```bash
python -m pytest tests -q
```

---

## 🔁 迁移说明

当前兼容行为包括：
- 旧 `state.json` 自动升级到 `CloseClaw Memory/state.json`
- 旧 `phase5` 配置键映射到 `orchestrator`
- 记忆产物可迁移到统一目录结构

---

## 🩺 故障排查

1. Web 搜索提示缺少 key
- 设置 `web_search.enabled=true`
- 设置 `web_search.provider=brave`
- 设置有效 `web_search.brave_api_key`

2. 工具调用意外要求审批
- 检查 `safety.default_need_auth`
- 检查工具级 `need_auth` 标注

3. Heartbeat 未触发
- 检查 `heartbeat.enabled`
- 检查 `CloseClaw Memory/HEARTBEAT.md`
- 检查 quiet-hours 与 queue guard 配置

4. Cron 未生效
- 检查 `cron.enabled`
- 检查 `cron.store_file` 写权限
- 使用 cron list/run-now 命令诊断

🪟 Windows 入口命令未识别

如果 PowerShell 提示找不到 `closeclaw`：

1. 先激活虚拟环境。
2. 重新执行可编辑安装，生成脚本入口。
3. 回退到模块模式运行。

```powershell
pip install -e .
python -m closeclaw agent --config config.yaml
Get-Command closeclaw
```

> ℹ️ 若 `Get-Command closeclaw` 无输出，说明当前 shell 的 PATH 未包含该入口脚本。

---

## 🤝 Contributing Guide

欢迎贡献，任何改进都非常有价值。

### 1) Fork 并创建功能分支

```bash
git checkout -b feat/your-change-name
```

### 2) 提交 PR 前先本地跑测试

聚焦测试：

```bash
python -m pytest tests/test_config.py -q
python -m pytest tests/test_tools.py tests/test_tool_execution_service.py -q
python -m pytest tests/test_runner.py tests/test_heartbeat_service.py tests/test_cron_service.py -q
```

全量测试：

```bash
python -m pytest tests -q
```

### 3) 提交 Pull Request

请包含：
- 清晰的问题描述与改动范围
- 改动内容与设计理由
- 测试证据（命令与结果）
- 若有行为/配置变更，附迁移说明

### 4) 当前重点欢迎的贡献方向

- 🐞 bug 发现、issue 提交与直接修复
- 🧪 channel/provider/integration 路径的测试覆盖增强
- 🪟🍎 跨平台稳定性提升（包括 macOS 兼容性改进）
- 📚 文档清晰度、上手体验与示例改进

### 5) 高质量 issue 建议

提交 bug 时建议附带：
- 运行命令与完整错误输出
- 可复现的最小配置（注意脱敏）
- 环境信息（OS、Python 版本、可选依赖安装情况）

感谢你帮助 CloseClaw 变得更好。

---

<p align="center">
  <b>CloseClaw：小而强，严谨防护，面向真实自动化。</b>
</p>
