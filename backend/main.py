from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.config import load_settings
from src.transport.http.fastapi.dependencies import (
    get_run_repository,
    get_cache,
    get_usage_ledger,
)
from src.transport.http.fastapi.errors import register_error_handlers
from src.transport.http.fastapi.middleware import CorrelationIdMiddleware, RequestLoggingMiddleware
from src.transport.http.fastapi.routers import pipeline, quiz, runs, usage
from src.transport.http.fastapi.schemas.usage import UsageAggregateResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize connections on startup."""
    # Initialize repository connection
    get_run_repository()
    # Initialize cache
    get_cache()
    # Initialize usage ledger
    get_usage_ledger()
    yield
    # Cleanup on shutdown if needed


app = FastAPI(
    title="Atlas API",
    version="1.0.0",
    description="FastAPI backend for the Atlas YouTube analysis platform.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
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
app.include_router(runs.router)
app.include_router(pipeline.router)
app.include_router(quiz.router)
app.include_router(usage.router)


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