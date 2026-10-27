# scripts

## Overview
Cross-platform helper scripts for setting up, building, running and testing Atlas. Every task has a bash (`.sh`) and a PowerShell (`.ps1`) version that do the same tasks, with some platform differences (e.g. `start.sh` uses `exec`; `dev.sh` stops the backend with `kill -INT`, while `dev.ps1` waits 10s then `taskkill /T /F`); `_common.sh` and `_common.ps1` are sourced helpers, not entry points.

## Interface
- `setup`: one-time install of Python deps (`uv sync --locked`), frontend deps (`npm ci`) and `.env` from `.env.example`.
- `build`: builds the frontend into `frontend/dist`.
- `start`: single-app mode, `python -m backend` serving API, worker and built UI on one port.
- `dev`: backend with `--reload` plus the Vite dev server, which proxies `/api` to the configured host and port.
- `test`: all quality checks: ruff, pytest, eslint, `tsc -b`, vitest.
- `_common.sh`: repo-root `cd`, `require` (tool check), `step` (logged command), `ensure_frontend_deps`.
- `_common.ps1`: the same roles as `Require-Command` and `Invoke-Step`; the `node_modules` check is inlined in `build`/`dev`/`test` `.ps1`. Also defines `$RepoRoot`, `$FrontendRoot`, `$Npm`, `$Npx` (prefers `npm.cmd`/`npx.cmd` to dodge the execution-policy-blocked `.ps1` shims).
- Both `_common` files export `PYTHONUTF8=1`; `_common.sh` also defines `UV_HINT` and `NODE_HINT` (install hints for `require`).
- `dev` honours a pre-set `ATLAS_API_URL`; otherwise it derives `http://<host>:<port>` from `load_settings()`.
- `test.sh` runs `tsc -b` via `bash -c "cd frontend && npx tsc -b"`; `start.ps1` runs `uv` then `exit $LASTEXITCODE` instead of `exec`.

## Invariants
- Scripts run from the repository root regardless of the caller's working directory.
- `.env` is never overwritten if it exists.
- `start` never builds; it only warns when `frontend/dist` is missing.
- `dev` validates config and port (`python -m backend --check`) before starting Vite.
- The `.sh` and `.ps1` versions of a task cover the same steps, with the platform differences noted above.

## Side Effects
- Installs packages into `.venv` and `frontend/node_modules`.
- Writes `frontend/dist`, `.env`, and `logs/backend-dev.log` / `logs/backend-dev.err.log` (overwritten each `dev` run).
- Starts long-running server processes; `dev` stops the backend on exit.

## Verification
- Run: `./scripts/test.sh` (Windows: `.\scripts\test.ps1`)
