<p align="center">
  <img src="assets/logo.png" alt="CloseClaw Logo" width="1200">
</p>

[EN](README.md) | [中文](README_zh.md)

<p align="center">
  <h1>Lightweight • Security-Focused • Practical Agent Runtime</h1>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/Runtime-Stable-1f883d">
  <img alt="Channels" src="https://img.shields.io/badge/Channels-CLI%20%7C%20Telegram%20%7C%20Feishu%20%7C%20Discord%20%7C%20WhatsApp%20%7C%20QQ-0a7ea4">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-f2cc60">
</p>

# Version 1.12.0. Welcome contributing!

> 🔥 **CloseClaw** is a lightweight, security-focused and practical OpenClaw-style Python framework. It is an personal agent for local and channel-based automation, with built-in guardrails, task scheduling, and memory infrastructure.

---

## ✨ Why CloseClaw

- ### ⚡ **Fast to deploy**
- Lightweight, Easy-to-Deploy, and equipped with a guarded orchestration loop to ensure strong performance. Powerful personal AI agents, ready in a minute.

- ### 🧠 **Memory management + Proactive execution**
- A fully replicated (OpenClaw) Memory Management System, Heartbeat and Cron services for scheduled/proactive execution. Long-term memory guaranteed. Fully customizable cognition.

- ### 🛡️ **Security focused**
- A clear and rigorous Sandbox Permission System, including an Agent-Review mechanism and command blacklist enforcement. Stop worrying about AI hallucinations deleting your inbox.

- ### 🔌 **MCP extensibility**
- Supporst MCP-Native Extensibility, Being compatible with any OpenClaw skills and tools. Massive ecosystem. Easy to arm your agent.

---

## 🎯 Run Modes

| Mode | What runs | Typical usage |
|---|---|---|
| `agent` | CLI only | Local interactive debugging / development |
| `gateway` | Non-CLI channels only | Bot gateway deployment |
| `all` | CLI + all enabled channels | Full local integration run |

---

## 📡 Channel Support

### 🛠️ Supported channels
- `cli`: 💻 For the purists. Fast, local, and pipe-friendly.
- `telegram`: ✈️ Your mobile command center. Secure and fast.(Recommended)
- `feishu / lark`: 🏢 Professional workflow integration for enterprise collaboration.
- `discord`: 🎮 Community-driven interactions with rich markdown support.
- `whatsapp` (bridge): 🟢 Reachable on the most locked-down mobile networks.
- `qq`: 🐧 Direct access to the classic Chinese social ecosystem.

### 🚥 Channel endpoint hints at startup
- Feishu: prints webhook address (host/port)
- WhatsApp: prints bridge URL
- Telegram / Discord / QQ: prints gateway/polling started hints

---

## 🤖 LLM Providers

- `openai` / `openai-compatible` (default-friendly)
- `gemini` (via LiteLLM runtime)
- `anthropic` (via LiteLLM runtime)

---

## 🚀 Quick Start

### 1) Install

```bash
git clone https://github.com/closeclaw/closeclaw.git
cd closeclaw
pip install -e .
```

Optional extras:

```bash
pip install -e ".[telegram]"
pip install -e ".[discord]"
pip install -e ".[whatsapp]"
pip install -e ".[qq]"
pip install -e ".[fastapi]"
pip install -e ".[providers]"
```

> 📝 `.[providers]` installs `litellm`, required for `gemini` and `anthropic` provider modes.

### 2) Create config

```bash
cp config.example.yaml config.yaml
```

Minimal config:

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

### 3) Run

Agent mode (CLI only):

```bash
closeclaw agent --config config.yaml
```

Gateway mode:

```bash
closeclaw gateway --config config.yaml
```

✨And that's your agent!

---

## 🐳 Docker (Optional)

Docker support is optional. Native Windows/Linux usage remains first-class. Please ensure that you have launched your docker engine before the following steps, and check your docker proxies to avoid network problems.



### 1) Prepare host files

```bash
cp .env.example .env
cp config.example.yaml config.yaml
mkdir -p workspace runtime-data
```

### 2) Build image

```bash
docker build -t closeclaw:local .
```

Install optional extras at build time:

```bash
docker build --build-arg INSTALL_EXTRAS="[providers,telegram,discord,whatsapp,qq]" -t closeclaw:local .
```

### 3) Run with docker run

Agent mode:

```bash
docker run --rm -it \
  -v ${PWD}/config.yaml:/app/config.yaml:ro \
  -v ${PWD}/workspace:/workspace \
  -v ${PWD}/runtime-data:/runtime-data \
  closeclaw:local agent --config /app/config.yaml
```

Gateway mode:

```bash
docker run -d --name closeclaw-gateway \
  -v ${PWD}/config.yaml:/app/config.yaml:ro \
  -v ${PWD}/workspace:/workspace \
  -v ${PWD}/runtime-data:/runtime-data \
  -p 9000:9000 \
  closeclaw:local gateway --config /app/config.yaml
```

### 4) Run with docker compose

```bash
docker compose build
docker compose up -d closeclaw-gateway
docker compose logs -f closeclaw-gateway
docker compose run --rm closeclaw-cli agent --config /app/config.yaml
docker compose down
```

### 5) Persistence and secret management

- `./workspace` maps to `/workspace` for your actual working files.
- `./runtime-data` keeps runtime state/memory across container restarts.
- `./config.yaml` is mounted read-only to `/app/config.yaml`.
- Secrets should go to `.env` and be referenced by `${ENV_VAR}` in config.
- In-container shell/file tools operate inside container namespace; bind-mount host paths you want tools to access.

### 6) Troubleshooting Docker path and permissions

- Permission denied writing runtime files:
  - Ensure host folders exist before first run (`workspace`, `runtime-data`).
  - Check host ownership and write permissions for mounted directories.
- Wrong workspace path behavior:
  - Set `WORKSPACE_ROOT: /workspace` in compose environment.
  - Keep `workspace_root` in config aligned with mounted path strategy.
- Healthcheck stays unhealthy:
  - Verify `config.yaml` exists and parses.
  - Verify required provider/channel dependencies are included in `INSTALL_EXTRAS`.

Operational hardening details are documented in [docs/Docker_Runbook.md](docs/Docker_Runbook.md).

---

## 🧱 Architecture Overview

```text
closeclaw/
├─ runner.py                              # Runtime entry + channel/heartbeat/cron orchestration
├─ agents/
│  └─ core.py                             # AgentCore: orchestration loop and execution lifecycle
├─ services/
│  ├─ tool_execution_service.py           # Tool routing + middleware + auth handling
│  └─ context_service.py                  # Context shaping, compaction, transcript windowing
├─ memory/
│  └─ memory_manager.py                   # Memory retrieval and persistence coordination
├─ channels/                              # CLI / Telegram / Feishu / Discord / WhatsApp / QQ adapters
├─ tools/                                 # File / Shell / Web / Scheduler / Memory helper tools
└─ mcp/                                   # MCP transport, pool, bridge, and health integration
```

### Core responsibilities
- `runner`: startup orchestration (channels, heartbeat, cron, agent lifecycle)
- `AgentCore`: orchestration loop + tool decision and execution flow
- `ToolExecutionService`: tool routing, middleware, auth interactions
- `ContextService`: transcript shaping and compaction policy
- `MemoryManager`: memory retrieval and persistence layer

### Main modules
- `closeclaw/runner.py`
- `closeclaw/agents/core.py`
- `closeclaw/services/tool_execution_service.py`
- `closeclaw/services/context_service.py`
- `closeclaw/memory/memory_manager.py`

---

## 🔐 Security Model

CloseClaw applies layered controls:

1. ✅ **Human authorization**
   - Tools with `need_auth=True` require explicit approval.
2. ✅ **Workspace sandbox**
   - File paths normalized and constrained to `workspace_root`.
3. ✅ **Command blacklist**
   - High-risk shell patterns blocked before execution.
4. ✅ **Audit logging**
   - Runtime operations logged to `safety.audit_log_path`.

---

## 🧰 Built-in Tooling

Tool groups:
- 📁 File tools: read/write/edit/delete/list/exists/size/line-range ops
- 🖥️ Shell tools: shell execution + pwd
- 🌍 Web tools: web_search + fetch_url
- ⏲️ Scheduler helper: call_cron
- 🧠 Memory helpers: write/edit memory file

Web search behavior:
- Provider currently targets Brave Search API
- Disabled by default until `web_search.enabled=true` and key is configured

---

## ⚙️ Configuration Reference

### `llm`
Key fields: `provider`, `model`, `api_key`, `base_url`, `temperature`, `max_tokens`, `timeout_seconds`

Gemini example:

```yaml
llm:
  provider: "gemini"
  model: "gemini-2.5-flash"
  api_key: "YOUR_GEMINI_API_KEY"
  temperature: 0.2
  max_tokens: 4096
```

Anthropic example:

```yaml
llm:
  provider: "anthropic"
  model: "claude-3-7-sonnet"
  api_key: "YOUR_ANTHROPIC_API_KEY"
  temperature: 0.2
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

### Other high-impact sections
- `safety`: admins, default auth, blacklist, audit settings
- `context_management`: token windows, thresholds, retention
- `orchestrator`: steps, wall-time, no-progress limits
- `heartbeat`: interval, quiet-hours, queue guards, routing
- `cron`: store path, timezone, enable/disable

### `Memory and Soul` (workspace personalization)

After first run, go to your workspace-local folder:

```text
<workspace_root>/CloseClaw Memory/
```

You can personalize runtime behavior by editing these files:
- `AGENTS.md`: agent policy/persona and collaboration preferences
- `SOUL.md`: long-term identity, tone, and behavioral style
- `USER.md`: user-specific preferences and constraints
- `TOOLS.md`: tool usage conventions and boundaries
- `SKILLS.md`: skill-level guidance and trigger conventions
- `HEARTBEAT.md`: periodic proactive behavior instructions
- `memory/YYYY-MM-DD.md`: day-level memory notes and context snapshots

Tips:
- Keep instructions concise, explicit, and conflict-free.
- Prefer stable rules in `SOUL.md` and task-scoped guidance in daily `memory/` notes.
- If behavior drifts, review `AGENTS.md` + `SOUL.md` first, then prune conflicting notes.

---

## 📦 MCP Setup Tutorial

CloseClaw supports:
- MCP server health diagnostics
- MCP tool projection via `MCPBridge`

### Step 0: Plan server layout

```text
<repo-root>/
  mcp_servers/
    weather_server/
    docs_server/
```

### Step 1: Prepare servers
- Python server in repo, or
- npm-hosted server via `npx` / `npx.cmd`

### Step 2: Configure in `config.yaml`

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

### Step 3: Verify health

```bash
closeclaw mcp --config config.yaml
closeclaw mcp --config config.yaml --json
```

### Step 4: Start runtime

```bash
closeclaw agent --config config.yaml
```

> ✅ Runner auto-loads configured MCP servers and syncs tool schemas into runtime.

### Step 5: Troubleshoot quickly
- stdio unhealthy: verify command and args manually
- http unhealthy: verify base_url + endpoint reachability
- config ignored: verify same file passed via `--config`
- tools not selected: check bootstrap path and tool-name conflicts

---

## 🧠 Memory Layout

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

Why this layout:
- Keeps operational artifacts out of source roots
- Makes backup and migration easier
- Supports deterministic upgrades from legacy scattered paths

---

## 🧪 Testing

Focused suites:

```bash
python -m pytest tests/test_config.py -q
python -m pytest tests/test_tools.py tests/test_tool_execution_service.py -q
python -m pytest tests/test_runner.py tests/test_heartbeat_service.py tests/test_cron_service.py -q
```

Full suite:

```bash
python -m pytest tests -q
```

---

## 🔁 Migration Notes

Compatibility behavior includes:
- legacy `state.json` upgrade to `CloseClaw Memory/state.json`
- legacy `phase5` config key mapped to `orchestrator`
- memory artifacts migrated into unified layout when possible

---

## 🩺 Troubleshooting

1. Web search key missing
- set `web_search.enabled=true`
- set `web_search.provider=brave`
- set valid `web_search.brave_api_key`

2. Tool calls require approval unexpectedly
- check `safety.default_need_auth`
- check tool-level `need_auth` behavior

3. Heartbeat not firing
- check `heartbeat.enabled`
- check `CloseClaw Memory/HEARTBEAT.md`
- check quiet-hours + queue guard settings

4. Cron inactive
- check `cron.enabled`
- check `cron.store_file` write permissions
- use cron list/run-now diagnostics

🪟 Windows Notes (Entry Command Not Found)

If PowerShell says `closeclaw` is not recognized:

1. Activate your virtual environment.
2. Reinstall editable package so scripts are generated.
3. Use module mode as fallback.

```powershell
pip install -e .
python -m closeclaw agent --config config.yaml
Get-Command closeclaw
```

> ℹ️ If `Get-Command closeclaw` returns nothing, current shell PATH does not include the generated entrypoint.

---

## 🤝 Contributing Guide

Contributions are welcome and appreciated.

### 1) Fork and create a feature branch

```bash
git checkout -b feat/your-change-name
```

### 2) Run tests locally before opening a Pull Request

Focused suites:

```bash
python -m pytest tests/test_config.py -q
python -m pytest tests/test_tools.py tests/test_tool_execution_service.py -q
python -m pytest tests/test_runner.py tests/test_heartbeat_service.py tests/test_cron_service.py -q
```

Full suite:

```bash
python -m pytest tests -q
```

### 3) Submit a Pull Request

Please include:
- clear problem statement and scope
- what changed and why
- test evidence (commands + results)
- migration notes if behavior/config changed

### 4) What we currently welcome most

- 🐞 bug discovery, issue reports, and direct fixes
- 🧪 stronger test coverage for channels/providers/integration paths
- 🪟🍎 cross-platform hardening, including macOS compatibility improvements
- 📚 documentation clarity, onboarding improvements, and examples

### 5) Issue quality checklist

For bug reports, please attach:
- runtime command used and full error output
- minimal config (redacted secrets)
- environment details (OS, Python version, optional dependencies)

Thanks for helping improve CloseClaw.

---



<p align="center">
  <b>CloseClaw: Small runtime, strong guardrails, serious automation.</b>
</p>
