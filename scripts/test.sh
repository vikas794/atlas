#!/usr/bin/env bash
# All quality checks: backend lint + tests, frontend lint + typecheck + unit tests.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require uv "$UV_HINT"
require node "$NODE_HINT"
ensure_frontend_deps

step "Backend lint (ruff)" uv run --locked ruff check .
step "Backend tests (pytest)" uv run --locked pytest -q
step "Frontend lint (eslint)" npm --prefix frontend run lint
step "Frontend typecheck (tsc -b)" bash -c "cd frontend && npx tsc -b"
step "Frontend tests (vitest)" npm --prefix frontend test
echo "All checks passed."
