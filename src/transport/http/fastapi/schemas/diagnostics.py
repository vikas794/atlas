"""``GET /api/diagnostics`` response (the frontend Diagnostics page is built against it)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DiagnosticCheck(BaseModel):
    #: config | database | youtube_key | llm_key | drive_credentials | drive_token | frontend_build
    id: str
    label: str
    status: Literal["ok", "warning", "error"]
    message: str


class RecentError(BaseModel):
    #: ISO 8601 with offset (UTC).
    timestamp: str
    level: str
    logger: str
    #: Redacted, single line.
    message: str


class DiagnosticsResponse(BaseModel):
    checks: list[DiagnosticCheck]
    #: The last 50 WARNING+ log records, newest first.
    recent_errors: list[RecentError]


__all__ = ["DiagnosticCheck", "DiagnosticsResponse", "RecentError"]
