#!/usr/bin/env bash
# Single-app mode: API + background worker + built UI on one port (config.yaml host/port,
# default http://127.0.0.1:8000). Nothing is built at runtime: run build.sh first.
# Stop with Ctrl+C or SIGTERM (graceful: the worker finishes or hands back its jobs).
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require uv "$UV_HINT"
if [ ! -f frontend/dist/index.html ]; then
  echo "WARNING: frontend/dist is missing: the API will run, but run ./scripts/build.sh to get the UI." >&2
fi
exec uv run --locked python -m backend
