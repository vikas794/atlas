from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.spa import SpaFallback, default_dist_dir
from src.config import load_settings
from src.shared.logging_setup import configure_logging
from src.transport.http.fastapi.dependencies import get_usage_ledger, get_worker
from src.transport.http.fastapi.errors import register_error_handlers
from src.transport.http.fastapi.middleware import RequestLoggingMiddleware
from src.transport.http.fastapi.routers import drive_auth, playlists, usage, videos

# Under uvicorn only uvicorn's own loggers are configured; without this, every src.* log line
# (including all pipeline events) is dropped. Honours log_level / log_file_logging / log_file.
# load_settings() validates the configuration and raises SettingsError (a readable list of
# problems) when it is invalid, so a bad config stops startup here.
_settings = load_settings()
configure_logging(_settings)


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


def install_cors(target: FastAPI, origins: list[str]) -> None:
    """Allow cross-origin calls only from the configured origins (none by default).

    Single-app mode is same-origin and the Vite dev server proxies /api, so CORS is only for
    an unusual setup (e.g. the UI served from another port without the proxy).
    """
    if not origins:
        return
    target.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Correlation-ID"],
    )


install_cors(app, _settings.cors_origin_list)

# Add custom middleware
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


# Single-app mode: when no API route matches, serve the built frontend (frontend/dist) with
# an SPA fallback. Unknown /api/* paths still get the JSON 404 envelope; see backend/spa.py.
app.router.default = SpaFallback(default_dist_dir())
