from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from src.config import load_settings
from src.shared.logging_setup import configure_logging
from src.transport.http.fastapi.dependencies import get_usage_ledger, get_worker
from src.transport.http.fastapi.errors import register_error_handlers
from src.transport.http.fastapi.middleware import (
    CorrelationIdMiddleware,
    RequestLoggingMiddleware,
)
from src.transport.http.fastapi.routers import drive_auth, playlists, usage, videos

# Under uvicorn only uvicorn's own loggers are configured; without this, every src.* log line
# (including all pipeline events) is dropped. Honours log_level / log_file_logging / log_file.
configure_logging(load_settings())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - run migrations and start the background worker.

    The worker (video processing, retries, publishing, crash recovery) lives inside this
    process, so it also runs in the Docker container started with ``uvicorn backend.main:app``.
    """
    # Initialize usage ledger
    get_usage_ledger()
    worker = get_worker()
    await worker.start()
    yield
    # Cleanup on shutdown
    await worker.stop()


app = FastAPI(
    title="Atlas API",
    version="1.0.0",
    description="FastAPI backend for the Atlas playlist-to-notes pipeline.",
    lifespan=lifespan,
)

_cors_origins_env = os.getenv("ATLAS_CORS_ORIGINS", "")
_cors_origins = [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add custom middleware
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(RequestLoggingMiddleware)

# Register error handlers
register_error_handlers(app)

# Include routers
app.include_router(usage.router)
app.include_router(drive_auth.router)
app.include_router(playlists.router)
app.include_router(videos.router)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "atlas-api"}


@app.post("/api/dev/shutdown")
def shutdown_development_server(
    background_tasks: BackgroundTasks,
    x_atlas_dev_token: str | None = Header(default=None),
) -> dict[str, str]:
    """Stop a locally launched development server after validating its ephemeral token."""
    settings = load_settings()
    expected_token = settings.dev_shutdown_token or os.getenv("ATLAS_DEV_SHUTDOWN_TOKEN")
    if not expected_token or x_atlas_dev_token != expected_token:
        raise HTTPException(status_code=404, detail="Not found")

    background_tasks.add_task(os._exit, 0)
    return {"status": "stopping"}


# Serve the built frontend SPA, if present (e.g. in the Docker image). Registered
# last so it never shadows the /api/* routers above.
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = _frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_frontend_dist / "index.html")
