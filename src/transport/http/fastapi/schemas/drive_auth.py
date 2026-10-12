from __future__ import annotations

from pydantic import BaseModel


class DriveStatusResponse(BaseModel):
    """Whether Google Drive is ready to receive exported notes."""

    #: True only when a valid (or refreshable) token exists.
    configured: bool
    message: str
    #: ``credentials_missing`` | ``needs_consent`` | ``authorized``.
    state: str = "credentials_missing"


class DriveActionResponse(BaseModel):
    """Result of a Drive credentials/auth action."""

    status: str
    message: str


__all__ = ["DriveActionResponse", "DriveStatusResponse"]
