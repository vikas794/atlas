# Shared helpers for the Atlas shell scripts (sourced; not run directly).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONUTF8=1

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required. $2" >&2
    exit 1
  fi
}

step() {
  echo "==> $1"
  shift
  "$@"
}

ensure_frontend_deps() {
  if [ ! -d frontend/node_modules ]; then
    step "Install frontend dependencies (npm ci)" npm --prefix frontend ci
  fi
}

UV_HINT="Install it from https://docs.astral.sh/uv/getting-started/installation/"
NODE_HINT="Install Node.js 20 or later from https://nodejs.org/"
