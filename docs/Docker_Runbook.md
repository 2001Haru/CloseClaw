# CloseClaw Docker Runbook

## 1. Positioning

Docker support is optional and complements native Windows/Linux usage.
Use containers when you want reproducible runtime dependencies and easier deployment handoff.

## 2. Recommended Host Layout

```text
<project-root>/
  config.yaml
  .env
  workspace/
  runtime-data/
```

- `workspace/` maps to `/workspace` for actual tool/file operations.
- `runtime-data/` keeps runtime state and memory persistent across restarts.
- `config.yaml` is mounted read-only into `/app/config.yaml`.

## 3. Build and Launch

```bash
cp .env.example .env
cp config.example.yaml config.yaml
mkdir -p workspace runtime-data

docker compose build
docker compose up -d closeclaw-gateway
```

Check status:

```bash
docker compose ps
docker compose logs -f closeclaw-gateway
```

Run one-shot CLI session:

```bash
docker compose run --rm closeclaw-cli agent --config /app/config.yaml
```

Stop services:

```bash
docker compose down
```

## 4. Production Hardening Notes

Current defaults include:

- Non-root runtime user in image (`closeclaw`).
- Healthcheck on both gateway and cli services.
- `no-new-privileges` security option.
- `init: true` to improve signal handling and process reaping.
- Baseline CPU/memory limits configurable via `.env`.
- `restart: unless-stopped` for gateway service.

Recommended operator actions:

- Set strict secrets in `.env` and never commit real keys.
- Right-size `GATEWAY_MEM_LIMIT` and `GATEWAY_CPUS` by workload.
- Pin image tags in deployment environments instead of `latest`.
- Keep host-mounted folders scoped to minimum required data.

## 5. Healthcheck Expectations

- Gateway healthcheck command:
  - `closeclaw provider --config /app/config.yaml --json`
- CLI healthcheck command:
  - `closeclaw --help`

If health status remains `unhealthy`:

1. Validate `/app/config.yaml` exists and is parseable.
2. Ensure required extras were installed (`INSTALL_EXTRAS`).
3. Inspect logs with `docker compose logs closeclaw-gateway`.

## 6. Common Troubleshooting

### Permission denied on mounted folders

- Pre-create host directories before `compose up`.
- Confirm host user has write permissions.
- On Linux/macOS, verify UID/GID mapping for mounted paths if needed.

### Gateway exits immediately

- Confirm at least one non-CLI channel is enabled for gateway mode.
- Check channel-specific token/webhook config.

### Tools cannot access expected host files

- Remember shell/file tools run in container namespace.
- Mount required host paths into container and reference mounted paths.

## 7. Smoke Validation

Use the repository smoke script:

```bash
chmod +x tests/test_docker.sh
tests/test_docker.sh
```

It validates:

- Docker image build
- Entrypoint command path
- Config bootstrap command path
- Gateway command path without immediate crash
