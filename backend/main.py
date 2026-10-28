from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.services.diagnostics import diagnostics_report
from backend.spa import SpaFallback, default_dist_dir
from src.config import load_settings
from src.infrastructure.google.drive import STATE_AUTHORIZED
from src.infrastructure.google.publisher import GoogleDrivePublisher
from src.shared.logging_setup import RECENT_ERRORS, configure_logging
from src.transport.http.fastapi.dependencies import (
    get_drive_exporter,
    get_drive_publisher,
    get_model_registry,
    get_usage_ledger,
    get_worker,
    remember_boot_settings,
)
from src.transport.http.fastapi.errors import register_error_handlers
from src.transport.http.fastapi.middleware import RequestLoggingMiddleware
from src.transport.http.fastapi.routers import (
    drive_auth,
    llm,
    playlists,
    settings,
    usage,
    videos,
)
from src.transport.http.fastapi.schemas.diagnostics import DiagnosticsResponse

# Under uvicorn only uvicorn's own loggers are configured; without this, every src.* log line
# (including all pipeline events) is dropped. Honours log_level / log_file_logging / log_file.
# load_settings() validates the configuration and raises SettingsError (a readable list of
# problems) when it is invalid, so a bad config stops startup here.
_settings = load_settings()
configure_logging(_settings)
remember_boot_settings(_settings)


async def _check_drive_root_folder() -> None:
    """Find or create the Drive "Atlas" folder at startup; never blocks or fails startup.

    Skipped when Drive is not authorized yet (the folder is then resolved at first publish).
    """
    try:
        if get_drive_exporter().auth_state()[0] != STATE_AUTHORIZED:
            return
        publisher = get_drive_publisher()
        if isinstance(publisher, GoogleDrivePublisher):
            await publisher.ensure_root_folder()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Drive startup check failed (%s); the Atlas folder is resolved at first publish",
            type(exc).__name__,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - run migrations and start the background worker.

    The worker (video processing, retries, publishing, crash recovery) lives inside this
    process, so it also runs in the Docker container started with ``uvicorn backend.main:app``.
    """
    # Initialize usage ledger
    get_usage_ledger()
    get_model_registry()  # migrate and seed the model registry now, not at the first note
    await _check_drive_root_folder()
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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
app.include_router(settings.router)
app.include_router(llm.router)
app.include_router(playlists.router)
app.include_router(videos.router)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Cheap liveness probe: the process is up. Readiness details live in /api/diagnostics."""
    return {"status": "ok", "service": "atlas-api"}


@app.get("/api/diagnostics", response_model=DiagnosticsResponse)
async def diagnostics() -> DiagnosticsResponse:
    """First-run health: config, database, keys (set / not set), Drive files, frontend build,
    plus the last 50 warnings/errors from the log (redacted). Never returns secret values."""
    report = await asyncio.to_thread(diagnostics_report, default_dist_dir())
    return DiagnosticsResponse.model_validate(report)


@app.delete("/api/diagnostics/recent-errors", status_code=204)
def clear_recent_errors() -> None:
    """Empty the in-memory recent warnings/errors buffer (logs/atlas.log is untouched)."""
    RECENT_ERRORS.clear()


# Single-app mode: when no API route matches, serve the built frontend (frontend/dist) with
# an SPA fallback. Unknown /api/* paths still get the JSON 404 envelope; see backend/spa.py.
app.router.default = SpaFallback(default_dist_dir())
