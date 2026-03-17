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

## 🚀 Detailed Agent Startup Guide

### Prerequisites

**System Requirements:**
- Python 3.10 or higher
- pip (Python package manager)
- 50+ MB disk space
- 100+ MB RAM

**Your Files:**
- `config.yaml` — Main configuration file (created from `config.example.yaml`)
- A dedicated `workspace_root` folder — where the agent can read/write files

### Step-by-Step Installation & Startup

#### Step 1: Install CloseClaw

```bash
# Navigate to project directory
cd /path/to/closeclaw

# Install in development mode (includes all dependencies)
pip install -e .

# (Optional) For Telegram support only
pip install -e ".[telegram]"

# Verify installation
python -c "import closeclaw; print('✅ CloseClaw installed successfully')"
```

#### Step 2: Prepare Workspace

```bash
# Create a dedicated folder for the agent
mkdir -p /path/to/agent_workspace
mkdir -p /path/to/agent_workspace/memory

# Initialize state files (agent creates these automatically)
# You can verify they were created after first run:
# - state.json: agent conversation state
# - audit.log: security audit log
# - interaction.md: interaction transcript
```

#### Step 3: Configure config.yaml

Copy and edit the configuration template:

```bash
cp config.example.yaml config.yaml
```

**Essential fields to customize:**

```yaml
agent_id: "closeclaw-main"                          # Unique agent identifier

workspace_root: "/absolute/path/to/agent_workspace" # ⚠️ Must be absolute path!

llm:
  provider: "openai-compatible"                     # or "openai", "deepseek", "ollama"
  model: "gpt-4"                                    # LLM model name
  api_key: "sk-your-actual-api-key-here"          # Direct string or ${ENV_VAR}
  base_url: "https://api.ohmygpt.com/v1"          # API endpoint

channels:
  - type: "cli"
    enabled: true                                   # Start with CLI first

  - type: "telegram"                                # Optional: enable later
    enabled: false
    token: "YOUR_BOT_TOKEN"

safety:
  admin_user_ids: ["cli_user"]                     # For CLI; use Telegram user ID later
  require_auth_for_zones: ["C"]                    # Zone C requires human approval

context_management:
  max_tokens: 100000                               # Adjust based on your LLM's limit
  warning_threshold: 0.75                          # Trigger Memory Flush at 75%
  critical_threshold: 0.95                         # Force compression at 95%
```

#### Step 4a: Run with CLI (Recommended for Testing)

```bash
# Start the agent listening on CLI
python -m closeclaw --config config.yaml

# You should see the interactive prompt:
# You > [type your message here]
```

**Interact with the agent:**
```
You > List files in workspace
Agent > Here are the files in your workspace...

You > Read config.yaml
Agent > [File contents]

You > Create a new file called test.txt with content "Hello World"
⚠️ Zone C Operation - Authorization Required
Author Tool: write_file
Arguments:
  path: "test.txt"
  content: "Hello World"

Approve? [Y/n]: y
✅ Approved
Agent > File created successfully.
```

**Exit the CLI:**
```
You > /exit
# or
You > /quit
```

#### Step 4b: Run with Telegram (Production)

1. **Create a Telegram Bot** (via @BotFather):
   - Get your bot token: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`

2. **Update config.yaml:**
   ```yaml
   channels:
     - type: "cli"
       enabled: false
     - type: "telegram"
       enabled: true
       token: "YOUR_BOT_TOKEN_HERE"
   
   safety:
     admin_user_ids: ["YOUR_TELEGRAM_USER_ID"]  # Get this from Telegram
   ```

3. **Start the agent:**
   ```bash
   python -m closeclaw --config config.yaml
   ```

4. **Send messages via Telegram:**
   - Open Telegram and chat with your bot
   - Zone C operations will prompt you with an inline keyboard: `[Approve]` `[Deny]`

#### Step 4c: Run with Feishu (Lark)

1. **Create a Feishu Bot** in your organization
2. **Get credentials:**
   - App ID
   - App Secret
   - Webhook URL (optional)

3. **Update config.yaml:**
   ```yaml
   channels:
     - type: "feishu"
       enabled: true
       token: "APP_ID_HERE"
       webhook_url: "APP_SECRET_HERE"
       metadata:
         verification_token: "VERIFICATION_TOKEN"
         webhook_port: 9000
   ```

4. **Start:**
   ```bash
   python -m closeclaw --config config.yaml
   ```

### Startup Verification Checklist

- [ ] `config.yaml` exists and is valid YAML
- [ ] `api_key` is set to your actual API key (not a placeholder)
- [ ] `workspace_root` points to an **existing folder**
- [ ] At least one channel is enabled (start with `cli: enabled: true`)
- [ ] `admin_user_ids` includes your user ID (CLI: `"cli_user"`)

**If startup fails:**

```bash
# Check Python version
python --version  # Should be 3.10+

# Verify imports
python -c "import closeclaw; from closeclaw.config import ConfigLoader; print('OK')"

# Test config load
python -c "from closeclaw.config import ConfigLoader; c = ConfigLoader.load('config.yaml'); print(f'Config loaded: {c.agent_id}')"

# Run with debug logging
LOG_LEVEL=DEBUG python -m closeclaw --config config.yaml
```

---

## 🧠 Memory Management & Context Window Configuration

### What is Context Window?

The **context window** is the maximum amount of conversation history the agent can see at once. For example:
- **100,000 tokens** → ~75 pages of conversation
- When you reach this limit, the agent **compresses or archives** old messages to make room for new ones

**Important distinction:**
- `llm.max_tokens` — How many tokens the LLM can **generate** (output length limit)
- `context_management.max_tokens` — How many tokens the agent can **see** (conversation history limit)

### Memory Management Settings

In your `config.yaml`, customize these:

```yaml
context_management:
  max_tokens: 100000              # Total conversation window (adjust to your LLM)
  warning_threshold: 0.75         # Auto-compress when reaching 75% usage
  critical_threshold: 0.95        # Force hard truncate at 95% usage
  
  summarize_window: 50            # Compress oldest 50 rounds into summary
  active_window: 10               # Always keep last 10 rounds raw (uncompressed)
  
  chunk_size: 5000                # Token limit per LLM summarization call
  retention_days: 90              # Archive memory files older than 90 days
```

### Parameter Explanations

| Parameter | Default | Meaning | When to Adjust |
|-----------|---------|---------|----------------|
| `max_tokens` | 100000 | Total context window budget (tokens) | If using Claude 200K → set to 180000; Ollama local → set to 8000 |
| `warning_threshold` | 0.75 | Trigger Memory Flush at 75% | Lower (0.60) = earlier flush; Higher (0.90) = squeeze more context |
| `critical_threshold` | 0.95 | Force hard truncate at 95% | Safety limit; don't exceed unless you know LLM can handle 100% usage |
| `summarize_window` | 50 | Compress oldest N rounds at once | Higher = fewer compressions; Lower = more frequent but gentler |
| `active_window` | 10 | Always keep last N rounds uncompressed | Keep recent context detailed; trade-off: less room for history |
| `chunk_size` | 5000 | Max tokens per compression call | Adjust if summarization LLM calls timeout |
| `retention_days` | 90 | Auto-delete memory files after N days | Set to 0 to keep forever; set to 30 for aggressive cleanup |

### Tuning for Different Scenarios

#### 💻 Local Development (Ollama, Small Models)

```yaml
context_management:
  max_tokens: 8000                # Small window for 7B models
  warning_threshold: 0.60         # Compress early (aggressive)
  critical_threshold: 0.80        # Hard stop at 80%
  active_window: 3                # Only keep 3 recent rounds
  summarize_window: 20            # Compress smaller batches
```

**Rationale:** Limited VRAM, so keep window small and compress frequently.

#### 🌐 Cloud API (OpenAI GPT-4, 128K Context)

```yaml
context_management:
  max_tokens: 110000              # Use 110K of 128K available
  warning_threshold: 0.80         # Compress at 80% (more headroom)
  critical_threshold: 0.95        # Hard stop at 95%
  active_window: 20               # Keep 20 rounds detailed
  summarize_window: 100           # Compress in larger batches (better summaries)
```

**Rationale:** High capacity, so keep more context and compress less often.

#### 🤓 Knowledge-Intensive Task (Long Research Sessions)

```yaml
context_management:
  max_tokens: 150000              # Maximize for long context
  warning_threshold: 0.82         # Compress a bit early
  critical_threshold: 0.98        # Push near limit
  active_window: 30               # Preserve recent details
  summarize_window: 200           # Large summarization batches
  retention_days: 180             # Keep memory longer
```

**Rationale:** Need to maintain complex context across many turns.

### What Happens During Memory Flush

When the agent reaches `warning_threshold` (e.g., 75% of 100K tokens):

1. **Recognition**: System detects high memory usage
2. **Ghost Prompt**: Agent receives hidden instruction: "Save important discussions to CSV/Markdown"
3. **Reply Interception**: Agent's `[SILENT_REPLY]` marker is detected
4. **Auto-Save**: Key takeaways automatically saved to `workspace_root/memory/`
5. **Context Reset**: Conversation history cleared; agent gets a fresh context window
6. **Notification**: User sees: "✅ Auto Memory Flush Completed — 3 files saved"

**Saved file example:**
```
workspace_root/
└── memory/
    └── flush_20260316_143045/
        ├── architecture_decisions.md      # Key design choices
        ├── implementation_code.md         # Code snippets
        └── discussion_summary.md          # Summary of 76 rounds
```

### Monitoring Context Usage

**In logs**, you'll see:
```
[CONTEXT] Token usage: 2700/100000 (2.70%), Status: OK
[CONTEXT] Token usage: 74500/100000 (74.50%), Status: WARNING
[MEMORY_FLUSH] Flush pending at 74.50%, will inject trigger prompt
[CONTEXT_COMPACTION] Applied 'summarize' compression. Original: 152 messages → 12
[CONTEXT] After compression: 31000/100000 (31.00%), Status: OK
```

**User sees (in chat):**
```
[CONTEXT MONITOR] Current token usage: 2700/100000 (2.70%)
```

### Best Practices

✅ **DO:**
- Set `max_tokens` to 70–80% of your LLM's actual context limit (leave 20–30% buffer)
- Monitor logs during first few sessions to calibrate thresholds
- Use high `active_window` (15–30) to keep recent context detailed
- Store important outputs in `workspace_root/memory/` manually if needed

❌ **DON'T:**
- Set `max_tokens` to 100% of LLM limit (no safety margin)
- Use very small `active_window` (<3) — loses recent context detail
- Mix `summarize_window` > `max_messages_in_history` (creates gaps)
- Ignore Memory Flush notifications (context can overflow)

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

## Workspace Configuration (Path Sandbox)

CloseClaw uses a strict **Path Sandbox**. The Agent is **only** allowed to read, write, or list files inside the directory specified by `workspace_root` in your `config.yaml`.

```yaml
# Example: Only allow the agent to access files in this specific folder
workspace_root: "D:/MyProjects/CloseClawWorkspace"
```

If the Agent tries to access files outside this directory (e.g., `D:/HALcode` or `../../etc/passwd`), the `PathSandbox` middleware will automatically **block** the operation and return an error to the Agent.

> 💡 **Tip**: Create an empty, dedicated folder for the Agent to work in (e.g., `mkdir ./agent_workspace`) and point `workspace_root` to its absolute path. Never set your entire disk (like `C:/` or `D:/`) as the workspace!

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

### Context Window Issues

#### Seeing "Token usage: X/100000" but config says 2000?
The 100,000 is from `context_management.max_tokens`, not `llm.max_tokens`.  
- `llm.max_tokens` → LLM output length  
- `context_management.max_tokens` → Conversation history window  

**Fix:** Edit `config.yaml` and adjust `context_management.max_tokens` to your desired value.

#### Memory Flush not triggering when expected
- Check `log_level: "DEBUG"` for detailed memory events
- Verify token counting is working: `[CONTEXT] Token usage: X/100000`
- Check that `warning_threshold` value makes sense (0.75 = 75%)

**Example for debugging:**
```bash
# Add to config.yaml
log_level: "DEBUG"

# Run and look for lines like:
# [MEMORY_FLUSH] Flush pending at 74.50%
# [CONTEXT_COMPACTION] Applied 'summarize' compression
```

#### Conversation gets compressed too early
- Increase `warning_threshold`: change from `0.75` to `0.85` (compress at 85% instead of 75%)
- Increase `max_tokens`: change from `100000` to `200000`

#### Memory files not saving during flush
- Check that `workspace_root` folder exists and is writable
- Check that `workspace_root/memory/` folder was created
- Look for `[MEMORY_FLUSH]` logs to verify flush was triggered
- Ensure LLM responded with `[SILENT_REPLY]` marker (should be automatic)

**Manual memory save:**
```bash
# You can manually create memory files in workspace_root/memory/
mkdir -p workspace_root/memory
echo "# Important discussion" > workspace_root/memory/my_notes.md
```

#### Out-of-Memory errors
If you see memory usage warnings:
1. Reduce `max_tokens` (e.g., `100000` → `50000`)
2. Reduce `active_window` (e.g., `10` → `5`)
3. Increase `summarize_window` (compress more aggressively)
4. Enable Memory Flush earlier: `warning_threshold: 0.70` (70% instead of 75%)

---

## Development Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | ✅ Complete | Core types, agent loop, middleware, config, tools, safety |
| Phase 2 | ✅ Complete | TaskManager, async tool routing, state persistence, CLI commands |
| Phase 3 | ✅ Complete | Channels (Telegram/Feishu/CLI), LLM providers, async shell, runner |
| Phase 3.5 | ✅ Complete | Transcript Repair firewall for error recovery |
| Phase 4 | ✅ Complete | Token counting, context management, memory flush, compression strategies |
| Phase 5 | 📋 Planned | SQLite + Hybrid Search RAG for long-term memory retrieval |

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
- Phase 3 Summary: [PHASE3_SUMMARY.md](PHASE3_SUMMARY.md)
- Phase 4 Memory Upgrade: [Phase4_Memory_Upgrade_Plan.md](Phase4_Memory_Upgrade_Plan.md)
- Verification Report: [VERIFICATION_COMPLETE_WORKFLOW.md](VERIFICATION_COMPLETE_WORKFLOW.md)
- Config Interface Fix: [CONFIG_INTERFACE_FIX.md](CONFIG_INTERFACE_FIX.md)
- Token Display Guide: [TOKEN_USAGE_DISPLAY.md](TOKEN_USAGE_DISPLAY.md)
