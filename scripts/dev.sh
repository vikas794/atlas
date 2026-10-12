#!/usr/bin/env bash
# Development: backend with auto-reload + Vite dev server (hot reload, proxies /api to the
# backend). Open http://127.0.0.1:5173. Ctrl+C stops both.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require uv "$UV_HINT"
require node "$NODE_HINT"
ensure_frontend_deps

# Fail before starting Vite if the config is invalid or the API port is taken (Vite would
# otherwise proxy /api to whatever else listens there).
step "Check configuration and port" uv run --locked python -m backend --check

uv run --locked python -m backend --reload &
BACKEND_PID=$!
stop_backend() {
  if kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill -INT "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap stop_backend EXIT INT TERM

npm --prefix frontend run dev
