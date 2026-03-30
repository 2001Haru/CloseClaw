# CloseClaw GUIDE (Detailed Deployment and Internals)

This document is the deep guide for deployment, architecture, and runtime internals.
Use `README.md` for quick start, and this GUIDE for production-grade setup, debugging, and extension.

## 1. Repository Architecture and Information Flow

### 1.1 Top-level architecture

```text
closeclaw/
  agents/          # AgentCore main loop and task manager
  channels/        # CLI / Telegram / Feishu / Discord / WhatsApp / QQ adapters
  cli/             # closeclaw command entry and health utilities
  compatibility/   # Tool schema adapters (legacy/native -> ToolSpecV2)
  context/         # Token counting and compaction primitives
  cron/            # Scheduled jobs store/service
  heartbeat/       # Periodic wake and HEARTBEAT.md execution control
  mcp/             # MCP clients, projection, bridge, pool health
  memory/          # Workspace memory layout, memory flush, vector memory DB
  middleware/      # SafetyGuard -> PathSandbox -> Auth/Guardian
  providers/       # LLM provider factory and implementations
  sandbox/         # OS-level sandbox execution backend (Windows restricted token)
  safety/          # Security mode enum, guardian, auth reasoning
  services/        # Prompt builder, context service, tool execution, runtime loop
  tools/           # Native tools (file/shell/web/cron/spawn)
```

### 1.2 Runtime core responsibilities

- `closeclaw/runner.py`
- Startup orchestrator.
- Creates LLM provider, middleware chain, channels, heartbeat, cron.
- Bootstraps MCP servers and projects MCP tools into runtime.

- `closeclaw/agents/core.py`
- Main agent runtime loop.
- Maintains message history, state restore/persist, tool call lifecycle.
- Handles authorization wait, resume, and interruption.

- `closeclaw/services/tool_execution_service.py`
- Single execution entry for both native and external tools.
- Normalizes tool schema to `ToolSpecV2`.
- Runs middleware checks and enforces authorization replay re-check.

- `closeclaw/services/context_service.py`
- Token usage analysis and threshold handling.
- Memory flush orchestration and compact memory snapshot injection.
- Transcript repair and memory retrieval helper.

- `closeclaw/memory/workspace_layout.py`
- Forces unified workspace memory root: `<workspace_root>/CloseClaw Memory`.
- Initializes baseline files and migrates legacy scattered artifacts.

### 1.3 Compatibility layer (`compatibility/`)

`compatibility` is an internal adapter boundary, not legacy dead code.

- `ToolSpecV2`: canonical runtime tool schema.
- `NativeAdapter`: converts native tool definitions to canonical schema.
- Used by `ToolExecutionService` and `ToolSchemaService` so runtime logic can treat native tools and MCP projected tools uniformly.

This keeps core runtime lightweight while supporting multiple tool ecosystems.

### 1.4 Persistent artifacts layout

By default all operational artifacts are under workspace memory root:

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

## 2. End-to-End Processing Flow

### 2.1 Startup flow

```text
closeclaw agent/gateway
  -> ConfigLoader.load()
  -> create LLM provider (and optional guardian-specific provider)
  -> create AgentCore
  -> build middleware chain
       SafetyGuard -> PathSandbox -> AuthPermissionMiddleware
  -> register native tools
  -> bootstrap MCP and register projected tools
  -> load state
  -> start heartbeat + cron services
  -> start enabled channels
```

### 2.2 One normal user turn

```text
Channel receives user message
  -> AgentCore builds prompt
     (system prompt + project context + skills + history + context monitor)
  -> LLM response (text + optional tool_calls)
  -> for each tool_call: ToolExecutionService
       -> middleware checks
       -> execute tool (native or MCP)
  -> append tool results
  -> continue loop until completion
  -> channel sends response (with token usage prefix)
```

### 2.3 Sensitive tool flow (authorization/security)

`need_auth` tool handling depends on `safety.security_mode`:

- `autonomous`
- No approval step, tool executes after middleware allow.

- `supervised`
- Middleware returns `requires_auth`.
- Runtime sends auth request to channel and waits user approval.
- On approve: `execute_authorized_request()` replays call and forces full middleware re-validation.

- `consensus`
- Middleware asks `ConsensusGuardian` (LLM sentinel).
- Approve: auto-allow (no manual user click).
- Reject, timeout, parse error, or guardian error: fail closed and block.

### 2.4 Heartbeat and cron wake flow

```text
HeartbeatService tick
  -> read <workspace_root>/CloseClaw Memory/HEARTBEAT.md
  -> decide run/skip
  -> enqueue system wake message into channel queue
  -> AgentCore processes wake message as normal turn
```

```text
CronService job due
  -> enqueue job message into channel queue
  -> AgentCore processes it as normal turn
```

The wake messages are standard runtime inputs, so all normal safety and tool policies still apply.

## 3. Agent Workflow Internals

### 3.1 Prompt composition layers

PromptBuilder composes:

1. Base `system_prompt` from config.
2. `[PROJECT CONTEXT]` from `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `SKILLS.md`.
3. `[WORK INFORMATION]` with UTC/local time and workspace paths.
4. Skills blocks (`ALWAYS SKILLS`, index summary).
5. Context monitor suffix (`current/max token ratio`).
6. Memory recall policy block when `retrieve_memory` tool exists.

### 3.2 Context management and memory compression

Context thresholds are controlled by `context_management`:

- `WARNING`: triggers memory flush mini-loop.
- `CRITICAL`: deterministic trim fallback keeps latest turns and injects compact snapshot.

Memory flush sequence:

```text
WARNING threshold hit
  -> inject flush system prompt
  -> agent writes/updates memory files via tools
  -> outputs [COMPACT_MEMORY_BLOCK] ... [/COMPACT_MEMORY_BLOCK]
  -> append [SILENT_REPLY]
  -> runtime captures normalized compact snapshot
  -> trim history to active window
```

### 3.3 Security stack (execution order)

Every tool call goes through layered gates:

1. `SafetyGuard`
- Shell pattern blacklist (balanced/strict profile).

2. `PathSandbox`
- Enforces file paths inside `workspace_root` for file-type tools.
- Resolves and normalizes relative paths.
- Blocks path traversal/out-of-workspace writes.

3. `AuthPermissionMiddleware`
- Interprets `need_auth` and current security mode.
- Builds auth reason and diff preview.
- Invokes Guardian in consensus mode.

4. OS-level sandbox (for configured protected tools)
- On Windows and protected tools (default `shell`), uses restricted token + MIC + JobObject isolation backend.

### 3.4 OS-level sandbox behavior

Configured by:

- `safety.os_sandbox_enabled`
- `safety.os_sandbox_protected_tools` (default `['shell']`)
- `safety.os_sandbox_fail_closed`

Behavior:

- If protected tool runs and backend is available: execute in restricted environment.
- If backend fails and `fail_closed=true`: block execution.
- If backend fails and `fail_closed=false`: fallback to normal execution path.

## 4. Advanced Configuration Guide

### 4.1 `llm` and guardian model override

Core fields:

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

Guardian dedicated model (optional):

```yaml
safety:
  security_mode: "consensus"
  consensus_guardian_provider: "gemini"
  consensus_guardian_model: "gemini-3-flash"
  consensus_guardian_api_key: "..."      # optional; fallback to llm.api_key
  consensus_guardian_base_url: "..."     # optional; fallback to llm.base_url
  consensus_guardian_timeout_seconds: 20.0
```

Fallback rule:
- If guardian provider/model missing or invalid, runtime falls back to main LLM provider.

### 4.2 `safety` recommended baseline

```yaml
safety:
  admin_user_ids: ["cli_user", "YOUR_TELEGRAM_USER_ID"]
  security_mode: "consensus"             # autonomous | supervised | consensus
  default_need_auth: false
  command_blacklist_enabled: true
  command_policy_profile: "balanced"     # balanced | strict
  custom_blacklist_rules: []

  os_sandbox_enabled: true
  os_sandbox_fail_closed: true            # recommended for strong fail-close
  os_sandbox_protected_tools: ["shell"]

  audit_log_enabled: true
  audit_log_path: "CloseClaw Memory/audit.log"
```

### 4.3 `context_management` tuning

- `max_tokens`: effective prompt budget.
- `warning_threshold`: start memory flush before hard overflow.
- `critical_threshold`: trim fallback trigger.
- `active_window`: how many recent rounds to keep with high fidelity.

For long-running agents, prefer higher `max_tokens` with conservative thresholds (for example 0.75/0.95).

### 4.4 `heartbeat` and `cron`

Heartbeat example:

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

Cron example:

```yaml
cron:
  enabled: true
  store_file: "CloseClaw Memory/cron_jobs.json"
  default_timezone: "UTC+08:00"
```

Note:
- `cron.store_file` is resolved relative to `workspace_root` if not absolute.

### 4.5 `mcp` projected tool behavior

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

Current projection defaults:

- `need_auth` defaults to `true` when MCP tool payload does not provide it.
- Unknown `tool_type` is normalized heuristically, then conservative fallback is `shell`.

### 4.6 Docker deployment config notes

Inside container:

- Set `workspace_root: "/workspace"`.
- Mount host `./workspace` -> `/workspace`.
- Mount host `./runtime-data` -> `/runtime-data`.

Do not use Windows host absolute paths in container config (for example `D:\...`) because container runtime is Linux namespace.

## 5. MCP and Docker Operational Playbook

### 5.1 MCP health and diagnostics

Use:

```bash
closeclaw mcp --config config.yaml
closeclaw mcp --config config.yaml --json
```

If unhealthy:

- Verify stdio command manually in the same environment.
- Verify HTTP base URL and endpoint reachability.
- Confirm same `config.yaml` is actually passed to runtime.

### 5.2 Docker startup (recommended sequence)

Windows PowerShell:

```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
New-Item -ItemType Directory -Force -Path workspace, runtime-data
```

Then:

```bash
docker compose build
docker compose up -d closeclaw-gateway
docker compose logs -f closeclaw-gateway
```

Health checks:

```bash
docker compose exec closeclaw-gateway closeclaw runtime-health --config /app/config.yaml --mode gateway --json
docker compose run --rm closeclaw-cli runtime-health --config /app/config.yaml --mode agent --json
```

Expected:
- Exit code `0`.
- JSON contains `"healthy": true`.

## 6. Troubleshooting

### 6.1 Config and startup

- Symptom: `workspace_root does not exist`
- Cause: invalid/missing path or container uses host path format.
- Fix: set a real existing path; in Docker use `/workspace`.

- Symptom: `No channels enabled for mode=gateway`
- Cause: only CLI channel enabled while running `gateway` mode.
- Fix: enable at least one non-CLI channel.

- Symptom: `python-telegram-bot is required`
- Cause: telegram extra dependency not installed.
- Fix: install `closeclaw[telegram]` or Docker `INSTALL_EXTRAS=[providers,telegram]` then rebuild.

### 6.2 Provider and auth

- Symptom: provider 401
- Cause: wrong API key/base_url/provider-model mismatch.
- Fix: verify `llm.provider`, `model`, `api_key`, `base_url`, and account permissions.

- Symptom: guardian blocks with timeout-like message
- Cause: guardian LLM call timeout.
- Fix: raise `consensus_guardian_timeout_seconds`, validate guardian provider override/network.

### 6.3 Distinguishing guardian timeout vs OS sandbox block

Guardian timeout block is returned as a Guardian decision path with reason code like `GUARDIAN_TIMEOUT`.

OS sandbox block for protected tool typically appears as tool execution failure with backend message in stderr, such as:

- `OS sandbox enforcement failed (blocked): ...` (fail-closed backend unavailable)
- restricted backend timeout/failure details

When checking logs/state, separate these two classes first before tuning policy.

### 6.4 Heartbeat and cron

- Symptom: heartbeat not firing
- Check:
  - `heartbeat.enabled`
  - `heartbeat.interval_s`
  - `CloseClaw Memory/HEARTBEAT.md` exists and not empty
  - quiet-hours and queue-busy guards are not suppressing ticks

- Symptom: cron job not executing
- Check:
  - `cron.enabled`
  - `cron.store_file` writable
  - timezone and schedule expression validity

Use CLI diagnostics:

```bash
closeclaw heartbeat-status --config config.yaml --json
closeclaw heartbeat-trigger --config config.yaml --json
closeclaw cron-list --config config.yaml --json
closeclaw cron-run-now <job_id> --config config.yaml --json
```

## 7. Validation Checklist (Recommended)

Before production rollout:

1. `closeclaw runtime-health --config config.yaml --mode gateway --json` returns healthy.
2. At least one real channel is enabled and dependency health is green.
3. `safety.security_mode` and `admin_user_ids` are correct for your policy.
4. `os_sandbox_fail_closed` is set according to your risk appetite.
5. `CloseClaw Memory` files exist under `workspace_root` and are writable.
6. MCP health check passes for all required servers.
7. Heartbeat and cron can be triggered manually once.

## 8. References

- Quick start: [README.md](README.md)
- Chinese quick start: [README_zh.md](README_zh.md)
- Docker operations: [docs/Docker_Runbook.md](docs/Docker_Runbook.md)
- WhatsApp bridge protocol: [docs/WhatsApp_Bridge_Protocol.md](docs/WhatsApp_Bridge_Protocol.md)



