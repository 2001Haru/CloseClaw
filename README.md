# CloseClaw - Lightweight & Safe AI Agent Framework

**CloseClaw** is a lightweight, modular, and security-focused Python implementation of the OpenClaw Agent framework. It prioritizes code transparency, user control, and operational safety.

## Features

### 🔒 **Three-Layer Security Model**

1. **HITL (Human-in-the-Loop)**: Zone C operations require explicit user approval via Telegram/Feishu/CLI
2. **Path Sandboxing**: All file operations restricted to configured workspace
3. **Command Blacklist**: Dangerous shell commands blocked before execution

### ⚡ **Lightweight Architecture**

- Synchronous agent loop (< 500 lines core code)
- Minimal dependencies (pydantic, httpx, pyyaml)
- Low memory footprint (< 50MB baseline)
- No Docker hard dependency

### 📱 **Multi-Channel Support**

- **Telegram**: International standard messaging
- **Feishu (Lark)**: Enterprise collaboration
- **CLI**: Local development mode

### 🛠️ **Simple Tool System**

Registered tools with decorator patterns:
```python
@tool(name="read_file", zone=Zone.ZONE_A)
async def read_file_impl(path: str) -> str:
    ...
```

### 📊 **Transparency**

- Machine-readable state (`state.json`)
- Human-readable interaction log (`interaction.md`)
- Complete audit trail (`audit.log`)

## Quick Start

### 1. Install

```bash
pip install closeclaw
```

Or install from source:
```bash
pip install -e .
```

### 2. Configure

Copy the example config:
```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:
- Set your OpenAI API key: `export OPENAI_API_KEY=sk-...`
- Set Telegram bot token: `export TELEGRAM_BOT_TOKEN=...`
- Set your Telegram user ID as admin
- Set workspace directory

### 3. Run

```python
from closeclaw import ConfigLoader, AgentCore

# Load configuration
config = ConfigLoader.load("config.yaml")

# Initialize agent (Phase 1 still WIP - full implementation in Phase 2-3)
# agent = AgentCore(
#     agent_id=config.agent_id,
#     llm_provider=...,  # Implement LLM bridge
#     config=config,
#     workspace_root=config.workspace_root,
# )
```

## Architecture

```
closeclaw/
├── agents/          # Agent core loop
│   └── core.py
├── channels/        # Communication (Phase 3)
├── tools/           # Tool implementations
│   ├── file_tools.py
│   ├── shell_tools.py
│   └── web_tools.py
├── middleware/      # Security filters
│   ├── __init__.py  (SafetyGuard, PathSandbox, ZoneBasedPermission)
├── types/           # Type definitions
├── safety/          # Audit logging
└── config.py        # Configuration system
```

## Security Model

### Trust Zones

- **Zone A**: Safe operations (read-only), auto-execute
- **Zone B**: Internal operations (logging), silent+log
- **Zone C**: Dangerous operations (writes, deletes, shell), require HITL

### Safeguards

| Layer | Mechanism | Examples |
|-------|-----------|----------|
| 1st   | HITL      | User confirmation via Telegram button |
| 2nd   | Path Sandbox | Prevent `../../etc/passwd` attacks |
| 3rd   | Command Blacklist | Block `del /s`, `format`, etc. |

## Development Status

- ✅ **Phase 1: Base Infrastructure** (Current)
  - Core types and enums
  - Agent loop engine (synchronous)
  - Middleware system
  - Configuration system
  - Tool system
  - Safety audit logging

- 🚀 **Phase 2: Agent Core Rewrite** (Coming)
  - Decorator-based tool registration
  - Middleware chain execution
  - HITL confirmation flow with Diff Preview
  - State persistence

- 📱 **Phase 3: Channels & Tools** (Coming)
  - Telegram integration
  - Feishu integration
  - CLI embedding
  - Enhanced tool implementations

- 🧪 **Phase 4: Testing & Optimization** (Later)
  - End-to-end tests
  - Performance tuning
  - Audit logging refinement

- 📚 **Phase 5: Documentation & Release** (Later)
  - User guide
  - API documentation
  - Example configurations

## Configuration Example

```yaml
agent_id: "closeclaw-main"
workspace_root: "/home/user/workspace"

llm:
  provider: "openai"
  model: "gpt-4"
  api_key: ${OPENAI_API_KEY}
  temperature: 0.0

channels:
  - type: "telegram"
    enabled: true
    token: ${TELEGRAM_BOT_TOKEN}
  - type: "cli"
    enabled: true

safety:
  admin_user_ids: ["YOUR_TELEGRAM_ID"]
  require_auth_for_zones: ["C"]
  command_blacklist_enabled: true
```

## File Operations Example

```python
# Read file (Zone A - auto-execute)
content = await agent.tools["read_file"].handler(path="config.yaml")

# Write file (Zone C - requires HITL approval)
result = await agent.tools["write_file"].handler(
    path="output.txt",
    content="New content"
)
# → Agent sends Diff Preview to user
# → User clicks [Yes/No]
# → If approved, file is written
```

## Troubleshooting

### "Module not found: closeclaw"
- Install the package: `pip install -e .`
- Or add the repo to PYTHONPATH: `export PYTHONPATH=$PYTHONPATH:/path/to/closeclaw`

### "Cannot import middleware"
- Ensure all `__init__.py` files are present in closeclaw/

## Contributing

Contributions welcome! Please:
1. Follow PEP 8 style (use `black` and `ruff`)
2. Add tests for new features
3. Update README with significant changes

## License

MIT License - See LICENSE file

## References

- OpenClaw Design: https://github.com/openclaw/openclaw-python
- Planning Document: [Planning.md](Planning.md)
- Security Model: [Planning.md - Security Section](Planning.md#安全与权限--三层防护)
