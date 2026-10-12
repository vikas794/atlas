from __future__ import annotations

import asyncio
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from src.infrastructure.google.drive import GoogleDriveExporter
from src.transport.http.fastapi.dependencies import get_drive_exporter
from src.transport.http.fastapi.errors import HTTPError
from src.transport.http.fastapi.schemas.drive_auth import (
    DriveActionResponse,
    DriveStatusResponse,
)

router = APIRouter(prefix="/api/drive", tags=["drive"])

ExporterDep = Annotated[GoogleDriveExporter, Depends(get_drive_exporter)]


@router.get("/status", response_model=DriveStatusResponse)
async def get_drive_status(exporter: ExporterDep) -> DriveStatusResponse:
    """Report Drive readiness: ``credentials_missing`` | ``needs_consent`` | ``authorized``.

    Never triggers an OAuth flow and makes no network call: it only inspects the credential
    and token files. ``configured`` is true only when a valid or refreshable token exists.
    """
    state, message = exporter.auth_state()
    return DriveStatusResponse(configured=state == "authorized", message=message, state=state)


@router.post("/credentials", response_model=DriveActionResponse)
async def upload_credentials(
    file: Annotated[UploadFile, File(...)],
    exporter: ExporterDep,
) -> DriveActionResponse:
    """Store the uploaded OAuth client secrets JSON file."""
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPError(
            status_code=400,
            detail="Must be a JSON file",
            error_code="DRIVE_INVALID_CREDENTIALS_FILE",
        )

    target = exporter.creds_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return DriveActionResponse(status="success", message="Credentials uploaded successfully.")


@router.post("/auth", response_model=DriveActionResponse)
async def authenticate_drive(exporter: ExporterDep) -> DriveActionResponse:
    """Run the interactive Google OAuth consent flow and persist the token.

    The flow opens a browser and waits for the redirect, so it runs in a worker thread: the
    event loop (and every other request) stays responsive while it waits. Run it on the
    machine that runs Atlas (see README).
    """
    try:
        await asyncio.to_thread(exporter.authenticate)
    except Exception as exc:
        raise HTTPError(
            status_code=400,
            detail=str(exc),
            error_code="DRIVE_AUTH_FAILED",
        ) from exc
    return DriveActionResponse(status="success", message="Authenticated successfully.")


__all__ = ["router"]
