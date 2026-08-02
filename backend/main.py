from __future__ import annotations

import os

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import pipeline, runs, quiz

app = FastAPI(
    title="Atlas API",
    version="1.0.0",
    description="FastAPI backend for the Atlas YouTube analysis platform.",
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

app.include_router(runs.router)
app.include_router(pipeline.router)
app.include_router(quiz.router)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "atlas-api"}


@app.post("/api/dev/shutdown")
def shutdown_development_server(
    background_tasks: BackgroundTasks,
    x_atlas_dev_token: str | None = Header(default=None),
) -> dict[str, str]:
    """Stop a locally launched development server after validating its ephemeral token."""
    expected_token = os.getenv("ATLAS_DEV_SHUTDOWN_TOKEN")
    if not expected_token or x_atlas_dev_token != expected_token:
        raise HTTPException(status_code=404, detail="Not found")

    background_tasks.add_task(os._exit, 0)
    return {"status": "stopping"}
