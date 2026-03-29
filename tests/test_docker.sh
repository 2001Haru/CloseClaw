#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE_TAG="${IMAGE_TAG:-closeclaw:docker-smoke}"
WORK_DIR="$(mktemp -d)"
DOCKER_USER="$(id -u):$(id -g)"
cleanup() {
  docker rm -f closeclaw-gateway-smoke >/dev/null 2>&1 || true
  chmod -R u+rwX "$WORK_DIR" >/dev/null 2>&1 || true
  find "$WORK_DIR" -type d -exec chmod 0777 {} + >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[docker-smoke] Building image: $IMAGE_TAG"
docker build --build-arg INSTALL_EXTRAS="[providers]" -t "$IMAGE_TAG" .

echo "[docker-smoke] Preparing test runtime directories"
mkdir -p "$WORK_DIR/workspace" "$WORK_DIR/runtime-data"
# Container runs as non-root user, so mounted temp dirs must be writable.
chmod 0777 "$WORK_DIR/workspace" "$WORK_DIR/runtime-data"
cat > "$WORK_DIR/config.yaml" <<'YAML'
agent_id: "docker-smoke"
workspace_root: "/workspace"
llm:
  provider: "openai-compatible"
  model: "gpt-4o-mini"
  api_key: "dummy"
  base_url: "https://api.openai.com/v1"
channels:
  - type: "cli"
    enabled: true
safety:
  admin_user_ids: ["cli_user"]
  default_need_auth: false
heartbeat:
  enabled: false
cron:
  enabled: false
YAML

echo "[docker-smoke] Verifying entrypoint command path"
docker run --rm --user "$DOCKER_USER" "$IMAGE_TAG" --help >/dev/null

echo "[docker-smoke] Verifying runtime health bootstrap in agent mode"
docker run --rm \
  --user "$DOCKER_USER" \
  -v "$WORK_DIR/config.yaml:/app/config.yaml:ro" \
  -v "$WORK_DIR/workspace:/workspace" \
  -v "$WORK_DIR/runtime-data:/runtime-data" \
  "$IMAGE_TAG" runtime-health --config /app/config.yaml --mode agent --json >/dev/null

echo "[docker-smoke] Verifying gateway command path (graceful startup summary/no-crash)"
docker run --rm \
  --user "$DOCKER_USER" \
  -v "$WORK_DIR/config.yaml:/app/config.yaml:ro" \
  -v "$WORK_DIR/workspace:/workspace" \
  -v "$WORK_DIR/runtime-data:/runtime-data" \
  "$IMAGE_TAG" gateway --config /app/config.yaml >/dev/null

echo "[docker-smoke] PASS"
