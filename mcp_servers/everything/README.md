# MCP Everything Test Server (Official)

## This is an EXAMPLE for MCP Server.
This folder provides a practical launcher for the official MCP test server:
`@modelcontextprotocol/server-everything`.

## Why this exists

- It is the official integration test server for MCP clients.
- It exposes a broad feature surface for end-to-end verification.
- It is ideal for smoke tests and transport/health checks.

## Prerequisites

- Node.js 18+ (or newer)
- `npx` available in PATH

## Start the server

PowerShell:

```powershell
./mcp_servers/everything/start_everything_server.ps1
```

CMD:

```cmd
mcp_servers\everything\start_everything_server.cmd
```

Optional version pinning:

PowerShell:

```powershell
./mcp_servers/everything/start_everything_server.ps1 -PackageVersion 0.6.0
```

CMD:

```cmd
mcp_servers\everything\start_everything_server.cmd 0.6.0
```

## Suggested CloseClaw config snippet

```yaml
mcp:
  servers:
    - id: everything_official
      transport: stdio
      command: npx
      args:
        - -y
        - "@modelcontextprotocol/server-everything"
      timeout_seconds: 30
```

## Validate from CloseClaw CLI

```bash
python -m closeclaw mcp-health --config config.yaml
python -m closeclaw mcp-health --config config.yaml --json
```
