# backend

## Overview
The single Atlas process: a FastAPI app that serves the JSON API, runs the background worker inside its lifespan, and serves the built frontend (`frontend/dist`) on the same port. The HTTP routers, schemas and middleware live in `src/transport/http/fastapi`; this package wires them together.

## Interface
- `main.app`: the FastAPI application (`uvicorn backend.main:app`). Mounts the `usage`, `drive_auth`, `settings`, `llm`, `playlists` and `videos` routers.
- `main.lifespan`: initializes the usage ledger and model registry, then starts and stops the worker.
- `main.install_cors(target, origins)`: adds CORS only when origins are configured; allowed methods are GET, POST, PUT, PATCH, DELETE and allowed headers are Content-Type, X-Correlation-ID.
- Built-in routes: `GET /api/health`, `GET /api/diagnostics`, `DELETE /api/diagnostics/recent-errors`.
- `__main__.main(argv)`: CLI entry point (`python -m backend`, flags `--reload`, `--check`). `port_problem(host, port)` explains why a port cannot be bound.
- `spa.SpaFallback`: ASGI default handler that serves `frontend/dist` with a client-side-routing fallback. Also `spa.is_api_path` and `spa.default_dist_dir`.
- [services](./services/README.md): first-run diagnostics checks.

## Invariants
- Settings are loaded and validated at import time of `backend.main`; invalid config stops startup (`python -m backend` exits with status 2 and lists the problems; it exits with status 1 when the port is busy or the host cannot be resolved, before `--check` is honoured).
- Unknown `/api/*` paths always get the JSON 404 envelope, never `index.html`.
- Static files are resolved and must stay inside `dist`; a missing asset is a plain 404, a missing build is a 503 help page.
- The worker runs in this process and is stopped before shutdown completes.
- CORS is off by default; credentials are never allowed.

## Side Effects
- Configures root logging (console, optional rotating file, in-memory recent-errors buffer). Under `--reload` the supervisor process logs to the console only; the server child configures file logging.
- Runs database migrations and seeds the model registry at startup.
- Starts a background worker that calls YouTube, LLM providers and Google Drive.
- `__main__` binds a socket to probe the port, then starts uvicorn.

## Verification
- Run: `uv run pytest -q tests/test_lifespan.py tests/test_spa_serving.py tests/test_run_entrypoint.py`
