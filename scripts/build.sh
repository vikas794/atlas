#!/usr/bin/env bash
# Build the frontend into frontend/dist, which `start` serves on the API port.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require node "$NODE_HINT"
ensure_frontend_deps
step "Build frontend (frontend/dist)" npm --prefix frontend run build
