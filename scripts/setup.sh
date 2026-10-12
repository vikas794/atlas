#!/usr/bin/env bash
# One-time setup: Python deps (uv), frontend deps (npm), and .env from the template.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require uv "$UV_HINT"
require node "$NODE_HINT"

step "Install Python dependencies (uv sync)" uv sync --locked
step "Install frontend dependencies (npm ci)" npm --prefix frontend ci

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example: add YOUTUBE_API_KEY and GEMINI_API_KEY."
else
  echo ".env already exists (left unchanged)."
fi

echo
echo "Setup done. The database is created and migrated on first start."
echo "  Build the UI once:    ./scripts/build.sh"
echo "  Run (one port):       ./scripts/start.sh"
echo "  Develop (hot reload): ./scripts/dev.sh"
