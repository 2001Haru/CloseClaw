# CloseClaw — Lightweight & Safe AI Agent Framework

**CloseClaw** is a lightweight, modular, and security-focused Python implementation of the OpenClaw Agent framework. It prioritizes code transparency, user control, and operational safety.

## Features

### 🔒 Three-Layer Security
1. **HITL (Human-in-the-Loop)**: Zone C operations require explicit user approval
2. **Path Sandboxing**: File operations restricted to configured workspace
3. **Command Blacklist**: Dangerous shell commands blocked before execution

### ⚡ Lightweight Architecture
- Synchronous agent loop (< 500 lines core)
- Minimal dependencies: `httpx`, `pyyaml`, `pydantic` (optional: `python-telegram-bot`)
- No openai SDK required — uses raw `httpx` for LLM API calls
- Low memory footprint (< 50MB baseline)

### 📱 Multi-Channel Support
- **CLI**: Interactive terminal mode for local development
- **Telegram**: Long polling + InlineKeyboard for HITL confirmation
- **Feishu (Lark)**: httpx REST API + Interactive Card for HITL

### 🤖 Third-Party LLM Support
Any OpenAI-compatible API endpoint works out of the box:
- OpenAI, OhMyGPT, DeepSeek, Ollama, Azure OpenAI, etc.
- Just set `provider`, `api_key`, and `base_url` in config

---

## Quick Start

### 1. Install

```bash
# Clone the repo
git clone https://github.com/closeclaw/closeclaw.git
cd closeclaw

# Install in development mode
pip install -e .

# (Optional) Install Telegram support
pip install -e ".[telegram]"
```

### 2. Configure

```bash
# Copy the template
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your settings:

```yaml
agent_id: "closeclaw-main"
workspace_root: "D:/your/workspace/path"  # Absolute path

llm:
  provider: "openai-compatible"    # or "openai", "ohmygpt", "deepseek", "ollama"
  model: "gpt-4"                  # Model name
  api_key: "sk-your-api-key"      # Direct string or ${ENV_VAR}
  base_url: "https://api.ohmygpt.com/v1"  # Third-party endpoint

channels:
  - type: "cli"
    enabled: true      # Start with CLI for testing
  - type: "telegram"
    enabled: false      # Enable later with real bot token

safety:
  admin_user_ids: ["cli_user"]     # For CLI; use Telegram user ID for Telegram
  require_auth_for_zones: ["C"]    # Zone C requires HITL approval
```

> ⚠️ **API Key Format**: Use a direct string like `"sk-xxx"`, NOT the `${...}` env var syntax, unless you've actually set the environment variable.

### 3. Run

```bash
# Start the agent with CLI channel
python -m closeclaw --config config.yaml
```

You should see:
```
════════════════════════════════════════════════════════════
  CloseClaw — Interactive CLI Mode
  Type your message and press Enter.
  Commands: /exit, /quit
════════════════════════════════════════════════════════════

You > Hello, what can you do?
Agent > I can help you with file operations, web fetching, and shell commands...
```

### 4. Try Some Operations

```
You > Read the file config.yaml                    # Zone A → auto-execute ✅
You > Write "hello" to test.txt                    # Zone C → HITL prompt ⚠️
  ⚠️ Zone C Operation — Authorization Required
  Tool: write_file
  Approve? [Y/n]: y
  ✅ Approved
Agent > File written: test.txt
```

---

## Architecture

```
closeclaw/
├── agents/
│   ├── core.py            # Agent loop engine (synchronous + TaskManager)
│   ├── task_manager.py    # Background task management
│   └── llm_providers.py   # OpenAI-compatible LLM client (httpx)
├── channels/
│   ├── base.py            # BaseChannel abstract interface
│   ├── cli_channel.py     # Interactive CLI channel
│   ├── telegram.py        # Telegram Bot channel
│   └── feishu.py          # Feishu REST API channel
├── tools/
│   ├── base.py            # Tool registry & decorators
│   ├── file_tools.py      # read/write/delete/list files
│   ├── shell_tools.py     # Async shell execution
│   └── web_tools.py       # Web search & URL fetching
├── middleware/             # Security middleware chain
│   └── __init__.py        # SafetyGuard, PathSandbox, ZoneBasedPermission
├── types/                  # Type definitions & enums
├── safety/                 # Audit logging
├── config.py               # YAML config loader
└── runner.py               # Multi-channel launcher
```

## Security Model

### Trust Zones

| Zone | Behavior | Examples |
|------|----------|----------|
| **A** | Auto-execute | Read file, list files, web search |
| **B** | Silent + log | Internal metadata, audit logs |
| **C** | HITL required | Write/delete files, shell commands |

### Three-Layer Safeguards

| Layer | Mechanism | Description |
|-------|-----------|-------------|
| 1st | SafetyGuard | Regex blacklist for dangerous commands |
| 2nd | PathSandbox | All file ops restricted to `workspace_root` |
| 3rd | ZoneBasedPermission | Zone C → HITL confirmation via channel UI |

---

## LLM Provider Configuration

CloseClaw uses **raw httpx** to call OpenAI-compatible APIs. No `openai` SDK dependency.

| Provider | `provider` value | Default `base_url` |
|----------|-----------------|-------------------|
| OpenAI | `"openai"` | `https://api.openai.com/v1` |
| OhMyGPT | `"ohmygpt"` | `https://api.ohmygpt.com/v1` |
| DeepSeek | `"deepseek"` | `https://api.deepseek.com/v1` |
| Ollama | `"ollama"` | `http://localhost:11434/v1` |
| Custom | `"openai-compatible"` | Must set `base_url` explicitly |

You can always override with an explicit `base_url` regardless of provider name.

---

## Development Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | ✅ Complete | Core types, agent loop, middleware, config, tools, safety |
| Phase 2 | ✅ Complete | TaskManager, async tool routing, state persistence, CLI commands |
| Phase 3 | ✅ Complete | Channels (Telegram/Feishu/CLI), LLM providers, async shell, runner |
| Phase 4 | 📋 Planned | Test suite cleanup, end-to-end tests, performance tuning |
| Phase 5 | 📋 Planned | Documentation, API docs, example configurations |

## Troubleshooting

### "Module not found: closeclaw"
```bash
pip install -e .
```

### LLM returns empty or errors
- Check your `api_key` is correct (direct string, not `${...}` unless env var is set)
- Check `base_url` matches your provider
- Try setting `log_level: "DEBUG"` in config for detailed HTTP logs

### Zone C operations always blocked
- Add your user ID to `safety.admin_user_ids` in config
- For CLI: the default user ID is `"cli_user"`

## Contributing

1. Follow PEP 8 (use `black` and `ruff`)
2. Add tests for new features
3. Update README with significant changes

## License

MIT License — See LICENSE file

## References

- Planning Document: [Planning.md](Planning.md)
- Phase 1 Summary: [PHASE1_SUMMARY.md](PHASE1_SUMMARY.md)
- Phase 2 Summary: [PHASE2_SUMMARY.md](PHASE2_SUMMARY.md)
