# CloseClaw

CloseClaw is a lightweight, security-focused Python implementation of OpenClaw.It is an agent framework for local and channel-based automation.

It includes:
- A guarded orchestration loop for tool-using agent turns
- Structured and Effective Memory Management
- Heartbeat and Cron services for scheduled/proactive execution
- MCP tooling for external server monitoring
- Human-in-the-loop authorization for sensitive actions
- Workspace sandboxing and command blacklist enforcement

## Why CloseClaw?

- CloseClaw is much more Lightweight and Easy-to-Deploy.
- CloseClaw has a fully replicated Memory Management System。
- CloseClaw supporst MCP-Native Extensibility.
- CloseClaw has a clear and rigorous Sandbox Permission System.

## Channels
- cli
- Telegram
- Feishu
- Discord
- WhatsApp (bridge)
- QQ

## LLM Providers
- OpenAI / OpenAI-compatible (default runtime)
- Gemini (via LiteLLM runtime)
- Anthropic (via LiteLLM runtime)

## Quick Start

### 1) Install

```bash
git clone https://github.com/closeclaw/closeclaw.git
cd closeclaw
pip install -e .
```

Optional extras:

```bash
pip install -e ".[telegram]"
pip install -e ".[fastapi]"
pip install -e ".[providers]"
```

Notes:
- `.[providers]` installs `litellm`, which is required for `llm.provider: gemini` and `llm.provider: anthropic`.
- If you only use `openai` or `openai-compatible`, base dependencies are sufficient.

### 2) Create config.yaml

```bash
cp config.example.yaml config.yaml
```

Minimum example:

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

```bash
closeclaw agent --config config.yaml
```

If `closeclaw` is not recognized in PowerShell:

1. Activate your virtual environment first.
2. Reinstall editable package so console scripts are generated:

```powershell
pip install -e .
```

3. Use module mode as fallback (always works in the active environment):

```powershell
python -m closeclaw agent --config config.yaml
```

Windows quick check:

```powershell
Get-Command closeclaw
```

If nothing is returned, current shell has no `closeclaw` entrypoint on PATH.

## Architecture Overview

Core runtime responsibilities:
- Runner: channel startup, heartbeat/cron startup, agent construction
- AgentCore: orchestration loop, tool execution, state transitions
- ToolExecutionService: tool routing + middleware + auth handling
- ContextService: compaction/flush orchestration and transcript shaping
- MemoryManager: vector + retrieval infrastructure

Main modules:
- closeclaw/runner.py
- closeclaw/agents/core.py
- closeclaw/services/tool_execution_service.py
- closeclaw/services/context_service.py
- closeclaw/memory/memory_manager.py

## Security Model

Security is implemented as layered controls:

1. Human authorization:
- Tools with need_auth=True require explicit approval
- default_need_auth in safety config can raise baseline strictness

2. Workspace path sandbox:
- File paths are normalized and constrained to workspace_root

3. Shell command blacklist:
- Dangerous command patterns are blocked before execution

4. Audit logging:
- Operations are recorded to safety.audit_log_path

Note:
- Older zone terminology (A/B/C) is legacy. The active runtime model is need_auth-driven.

## Tooling

Built-in tools are registered via decorators in closeclaw/tools.

Primary tool groups:
- File: read, write, append, delete, list, exists, size, line-range deletion
- Shell: shell execution, pwd
- Web: web_search, fetch_url
- Cron helper: call_cron
- Memory helpers: write_memory_file, append_memory_file

Web search behavior:
- Provider support currently targets Brave Search API
- Disabled by default unless web_search.enabled=true and key is set

## Configuration Reference

High-impact sections in config.yaml:

### llm
- provider, model, api_key, base_url
- temperature, max_tokens, timeout_seconds

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

### Migration: openai-compatible -> multi-provider

If you are currently using `openai-compatible`, migrate with this checklist:

1. Install provider runtime extras:

```bash
pip install -e ".[providers]"
```

2. Update `llm.provider` and `llm.model`:
- Gemini: `provider: "gemini"`, `model: "gemini-2.5-flash"`
- Anthropic: `provider: "anthropic"`, `model: "claude-3-7-sonnet"`

3. Keep `api_key` in the same `llm` block.

4. Optional: remove `base_url` unless you need custom proxy/gateway routing.

5. Restart agent and verify startup logs include selected provider/model.

### web_search

```yaml
web_search:
  enabled: false
  provider: "brave"
  brave_api_key: "BSA-..."
  timeout_seconds: 30
```

### safety
- admin_user_ids
- default_need_auth
- command_blacklist_enabled
- custom_blacklist_rules
- audit_log_enabled, audit_log_path, audit_log_retention_days

### context_management
- max_tokens
- warning_threshold, critical_threshold
- summarize_window, active_window
- chunk_size, retention_days

### orchestrator
- max_steps
- max_tokens_per_run
- max_wall_time_seconds
- no_progress_limit
- telemetry, rollout

### heartbeat
- enabled, interval_s
- quiet_hours: enabled, timezone, ranges
- queue_busy_guard: enabled, max_queue_size
- routing: target_ttl_s, fallback_channel, fallback_chat_id
- notify: enabled

### cron
- enabled
- store_file
- default_timezone

## Memory Layout

CloseClaw maintains workspace-local state in a unified directory:

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
- Keeps operational artifacts out of project roots
- Makes backup/inspection straightforward
- Supports deterministic migration from older scattered paths

## Channels

Supported channels:
- cli
- telegram
- feishu
- discord
- whatsapp
- qq

Each channel has independent input/output transport, while sharing one AgentCore runtime.

## Heartbeat And Cron 

Heartbeat:
- Periodically reads CloseClaw Memory/HEARTBEAT.md
- Applies quiet-hours and queue-busy guards
- Can be triggered manually via CLI command

Cron:
- Supports at, every, cron schedule modes
- Persists jobs to cron.store_file
- Supports enable/disable/remove/run-now flows

### CLI Commands

Recommended style (short commands):

```bash
closeclaw agent --config config.yaml
closeclaw list --state "CloseClaw Memory/state.json"
closeclaw show #001 --state "CloseClaw Memory/state.json"
closeclaw stop #001 --state "CloseClaw Memory/state.json"
closeclaw summary --state "CloseClaw Memory/state.json"
```

Channel and provider health:

```bash
closeclaw channel --config config.yaml
closeclaw channel --config config.yaml --name discord --json
closeclaw provider --config config.yaml
closeclaw provider --config config.yaml --name openai --json
```

Other health and scheduler commands:

```bash
closeclaw mcp --config config.yaml
closeclaw heartbeat-trigger --config config.yaml
closeclaw heartbeat-status --config config.yaml
closeclaw cron-list --config config.yaml
```

Long command names are still supported for compatibility (`tasks`, `task`, `cancel`, `mcp-health`, `channel-health`, `provider-health`).

Module-mode equivalents (if shell cannot find `closeclaw`):

```bash
python -m closeclaw agent --config config.yaml
python -m closeclaw list --state "CloseClaw Memory/state.json"
python -m closeclaw channel --config config.yaml
python -m closeclaw provider --config config.yaml
```

## MCP Server Setup Tutorial

CloseClaw currently provides two MCP capabilities:
- Server health diagnostics via `mcp-health`
- MCP tool projection primitives (`MCPBridge`) for advanced runtime integration

### Step 0: Decide where MCP server code lives in your repository

Recommended layout for local MCP servers:

```text
<repo-root>/
  mcp_servers/
    weather_server/
      ...
    docs_server/
      ...
```

Notes:
- This folder is a convention, not a hard requirement.
- `stdio` transport launches subprocesses from the current working directory, so stable relative paths are easiest when server code is inside your repository.
- For npm-hosted servers (for example via `npx`), you do not need to store server source in repo.

### Step 1: Install or prepare MCP servers

Option A: Python MCP server in repository

```bash
cd <repo-root>
mkdir -p mcp_servers
# Put your python MCP server package/code under mcp_servers/
```

Option B: Node MCP server from npm registry

```bash
# No repo folder required; configure command as npx/npx.cmd in config.yaml
```

### Step 2: Add MCP servers to config.yaml

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

Field notes:
- `id`: unique MCP server name shown in health output
- `transport`: `stdio` for local subprocess, `http` for remote service
- `command` + `args`: command line used to start stdio server process
- `timeout_seconds`: per-request timeout
- `max_retries` and `retry_backoff_seconds`: http transport retry policy

### Step 3: Verify MCP connectivity

Run your MCP Server, and then check the connectivity as followss:

```bash
python -m closeclaw mcp-health --config config.yaml
python -m closeclaw mcp-health --config config.yaml --json
```

Expected behavior:
- When configured correctly, each server appears with health and metrics
- If no servers are configured, output shows `No MCP servers configured.`

### Step 4: Let agent call MCP tools in conversations

Default runner behavior:
- On startup, runner reads `mcp.servers` from `config.yaml`
- Runner syncs MCP tools into runtime automatically
- Synced MCP tools are included in the tool schema list sent to the model

So after configuring `mcp.servers` and starting with:

```bash
python -m closeclaw --config config.yaml
```

the agent can directly choose and call MCP-provided tools during normal conversation.

For custom runtimes (without the default runner), you can still bootstrap manually:

```python
from closeclaw.config import ConfigLoader
from closeclaw.runner import create_agent
from closeclaw.mcp import MCPBridge, MCPClientPool
from closeclaw.mcp.transport import MCPStdioClient, MCPHttpClient

config = ConfigLoader.load("config.yaml")
agent = create_agent(config)

pool = MCPClientPool()
pool.register("local-stdio", MCPStdioClient(command="python", args=["-m", "your_mcp_server"]))
pool.register("remote-http", MCPHttpClient(base_url="https://example.com", endpoint="/mcp"))

bridge = MCPBridge(pool)
await bridge.sync_server_tools("local-stdio", agent.tool_execution_service)
await bridge.sync_server_tools("remote-http", agent.tool_execution_service)
```

After sync, projected MCP tools are executable by tool name through `ToolExecutionService`.

### Step 5: Troubleshoot common issues

1. `stdio` server unhealthy:
- Check `command` and `args` can run manually in your shell
- Increase `timeout_seconds` if startup is slow

2. `http` server unhealthy:
- Verify `base_url` and `endpoint` combination is reachable
- Check TLS/auth/network policy for the target host
- Tune `max_retries` and `retry_backoff_seconds` for unstable networks

3. Config appears ignored:
- Make sure you are passing the same file via `--config`
- Confirm YAML indentation and key names under `mcp.servers`

4. MCP tools can be synced but agent never chooses them:
- Confirm you started agent via default runner, or your custom runtime mirrors runner MCP bootstrap flow
- Verify projected tool names do not conflict with existing built-in tool names

## Testing

Run focused suites:

```bash
python -m pytest tests/test_config.py -q
python -m pytest tests/test_tools.py tests/test_tool_execution_service.py -q
python -m pytest tests/test_runner.py tests/test_heartbeat_service.py tests/test_cron_service.py -q
```

Run full suite:

```bash
python -m pytest tests -q
```

## Migration Notes

Compatibility behavior currently included in code:
- legacy state.json path can be upgraded to CloseClaw Memory/state.json
- legacy phase5 config key is still accepted and mapped to orchestrator
- workspace memory artifacts are migrated into unified layout when possible

## Troubleshooting

1. Web search returns key-missing message:
- Set web_search.enabled=true
- Set web_search.provider=brave
- Set web_search.brave_api_key to a valid Brave key

2. Tool calls require approval unexpectedly:
- Check safety.default_need_auth
- Check the specific tool's need_auth behavior

3. Heartbeat not firing:
- Verify heartbeat.enabled
- Verify HEARTBEAT.md exists under CloseClaw Memory
- Check quiet_hours and queue_busy_guard settings

4. Cron appears inactive:
- Verify cron.enabled
- Verify cron.store_file path and write permissions
- Use cron-list and cron-run-now for diagnostics

## Project Status

Current repository direction includes:
- Orchestrator and auth model stabilization
- Heartbeat/Cron operational flow
- Memory layout and prompt context hardening
- Brave-backed configurable web search

For implementation details and milestone snapshots, review:
- PHASE4_Patch.md
- PHASE5_SUMMARY.md
- PHASE6_SUMMARY.md
- Phase6_Heartbeat_Upgrade_Plan.md
