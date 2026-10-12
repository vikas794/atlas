from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from src.infrastructure.google.drive import GoogleDriveExporter
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

    Never triggers an OAuth flow and makes no network call: it only inspects the credential
    and token files. ``configured`` is true only when a valid or refreshable token exists.
    """
    state, message = exporter.auth_state()
    return DriveStatusResponse(configured=state == "authorized", message=message, state=state)


#: A Google OAuth client file is well under 1 KiB; anything this large is not one.
MAX_CREDENTIALS_BYTES = 64 * 1024


def _invalid_credentials(message: str) -> HTTPError:
    return HTTPError(status_code=400, detail=message, error_code="DRIVE_INVALID_CREDENTIALS_FILE")


def parse_client_secrets(raw: bytes) -> dict:
    """Validate an uploaded OAuth client file; return the parsed JSON or raise ``HTTPError``.

    Google's "Desktop app" download is ``{"installed": {...}}`` (``{"web": {...}}`` for a web
    client); both must carry ``client_id`` and ``client_secret``.
    """
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid_credentials("The file is not valid JSON.") from None
    section = None
    if isinstance(data, dict):
        section = data.get("installed") or data.get("web")
    if not isinstance(section, dict):
        raise _invalid_credentials(
            "This is not a Google OAuth client file: expected an 'installed' (Desktop app) "
            "or 'web' section. Download it from Google Cloud > APIs & Services > Credentials."
        )
    missing = [key for key in ("client_id", "client_secret") if not section.get(key)]
    if missing:
        raise _invalid_credentials(f"The OAuth client file is missing: {', '.join(missing)}.")
    return data


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
    parse_client_secrets(raw)

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
