from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from src.infrastructure.google.drive import (
    STATE_AUTHORIZED,
    STATE_NEEDS_CONSENT,
    GoogleDriveAuthorizationRequired,
    GoogleDriveExporter,
)
from src.shared.oauth_client import client_secrets_problem
from src.shared.text import atomic_write
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

    Never triggers an OAuth flow. ``configured`` is true only when a valid or refreshable
    token exists. An expired token that *looks* refreshable is verified with Google (the same
    check as ``/api/diagnostics``), since a revoked one would otherwise read as "connected".
    A network failure there is not a verdict: the state stays ``authorized``.
    """
    state, message = exporter.auth_state()
    if state == STATE_AUTHORIZED:
        try:
            await asyncio.to_thread(exporter.ensure_authorized)
        except GoogleDriveAuthorizationRequired as exc:
            state, message = STATE_NEEDS_CONSENT, str(exc)
        except Exception:  # network down, Google 5xx: unknown, not revoked
            message = f"{message} Could not reach Google to verify it just now."
    return DriveStatusResponse(configured=state == STATE_AUTHORIZED, message=message, state=state)


#: A Google OAuth client file is well under 1 KiB; anything this large is not one.
MAX_CREDENTIALS_BYTES = 64 * 1024


def _invalid_credentials(message: str) -> HTTPError:
    return HTTPError(status_code=400, detail=message, error_code="DRIVE_INVALID_CREDENTIALS_FILE")


@router.post("/credentials", response_model=DriveActionResponse)
async def upload_credentials(
    file: Annotated[UploadFile, File(...)],
    exporter: ExporterDep,
) -> DriveActionResponse:
    """Store the uploaded OAuth client secrets JSON at the configured ``google_creds_path``.

    The upload's filename never decides the destination (no path can be injected); the
    content is size-limited, validated as a Google OAuth client file and written atomically,
    so a bad upload never replaces a working file.
    """
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise _invalid_credentials("Must be a JSON file")

    raw = await file.read(MAX_CREDENTIALS_BYTES + 1)
    if len(raw) > MAX_CREDENTIALS_BYTES:
        raise HTTPError(
            status_code=413,
            detail=f"The file is larger than {MAX_CREDENTIALS_BYTES // 1024} KiB; "
            "a Google OAuth client file is much smaller.",
            error_code="DRIVE_CREDENTIALS_TOO_LARGE",
        )
    problem = client_secrets_problem(raw)
    if problem:
        raise _invalid_credentials(problem)

    target = exporter.creds_path
    await asyncio.to_thread(atomic_write, target, raw)
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)  # owner-only on POSIX; on Windows it only clears read-only
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
