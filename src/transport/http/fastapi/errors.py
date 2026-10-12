from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.domain.exceptions import (
    CacheError,
    ConfigurationError,
    ConflictError,
    DomainError,
    NotFoundError,
    ProviderError,
    StorageError,
)
from src.domain.interfaces.drive_publisher import DriveAuthorizationRequired
from src.domain.models.playlist import FailureKind, FailureType, PipelineError
from src.domain.services.stage_machine import InvalidTransitionError

logger = logging.getLogger(__name__)


class HTTPError(Exception):
    """Base HTTP error with status code and detail."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: str | None = None,
        details: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        self.details = details or {}
        super().__init__(detail)


def error_envelope(
    code: str, message: str, details: dict | None = None
) -> dict[str, object]:
    """Build the PRD error body: ``{code, message, details}``.

    ``details`` is always present; callers without extra context pass ``{}``.
    """
    return {
        "code": code,
        "message": message,
        "details": jsonable_encoder(details or {}),
    }


def domain_error_to_http(exc: DomainError) -> HTTPError:
    """Map domain errors to HTTP errors (PRD section 16: proper status codes, code/message/details)."""
    if isinstance(exc, DriveAuthorizationRequired):
        return HTTPError(
            status_code=409,
            detail=str(exc),
            error_code="DRIVE_AUTH_REQUIRED",
            details={
                "hint": "Authorize Google Drive first: upload credentials with "
                "POST /api/drive/credentials, then run POST /api/drive/auth."
            },
        )
    if isinstance(exc, ConfigurationError):
        return HTTPError(status_code=503, detail=str(exc), error_code="CONFIG_MISSING")
    if isinstance(exc, NotFoundError):
        return HTTPError(status_code=404, detail=str(exc), error_code="NOT_FOUND")
    if isinstance(exc, InvalidTransitionError):
        return HTTPError(
            status_code=409,
            detail=str(exc),
            error_code="INVALID_STATE",
            details={"current": str(exc.current), "requested": str(exc.target)},
        )
    if isinstance(exc, ConflictError):
        return HTTPError(status_code=409, detail=str(exc), error_code="CONFLICT")
    if isinstance(exc, ProviderError):
        return HTTPError(
            status_code=502,
            detail=str(exc),
            error_code=f"PROVIDER_ERROR_{exc.provider.upper() if exc.provider else 'UNKNOWN'}",
        )
    if isinstance(exc, CacheError):
        return HTTPError(
            status_code=503,
            detail="Cache service temporarily unavailable",
            error_code="CACHE_ERROR",
        )
    if isinstance(exc, StorageError):
        return HTTPError(
            status_code=500,
            detail="Storage operation failed",
            error_code="STORAGE_ERROR",
        )
    return HTTPError(
        status_code=500,
        detail=str(exc),
        error_code="DOMAIN_ERROR",
    )


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """FastAPI exception handler for DomainError."""
    http_error = domain_error_to_http(exc)
    logger.warning(
        "Domain error in %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=http_error.status_code >= 500,
    )
    return JSONResponse(
        status_code=http_error.status_code,
        content=error_envelope(
            http_error.error_code or "DOMAIN_ERROR",
            http_error.detail,
            http_error.details,
        ),
    )


async def http_error_handler(request: Request, exc: HTTPError) -> JSONResponse:
    """FastAPI exception handler for HTTPError."""
    logger.warning(
        "HTTP error in %s %s: %s",
        request.method,
        request.url.path,
        exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(
            exc.error_code or "HTTP_ERROR",
            exc.detail,
            exc.details,
        ),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI exception handler for validation errors."""
    logger.warning(
        "Validation error in %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content=error_envelope(
            "VALIDATION_ERROR",
            "Request validation failed",
            {"errors": exc.errors()},
        ),
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for unhandled exceptions."""
    logger.exception(
        "Unhandled error in %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_envelope(
            "INTERNAL_ERROR",
            "An unexpected error occurred",
        ),
    )


async def starlette_http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Wrap Starlette/FastAPI ``HTTPException`` in the {code, message, details} envelope.

    Framework-raised 404s (unmatched routes) and the explicit ``HTTPException``
    raises in ``backend/main.py`` otherwise leak FastAPI's ``{"detail": ...}``
    shape, which the frontend's ApiError parser cannot read.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        # Preserve structured detail as-is; it already carries the real message.
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(f"HTTP_{exc.status_code}", "", detail),
        )
    logger.warning(
        "HTTP %s in %s %s: %s",
        exc.status_code,
        request.method,
        request.url.path,
        detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(f"HTTP_{exc.status_code}", str(detail)),
    )

async def pipeline_error_handler(request: Request, exc: PipelineError) -> JSONResponse:
    """FastAPI exception handler for PipelineError."""
    logger.warning(
        "Pipeline error in %s %s: %s",
        request.method,
        request.url.path,
        exc,
    )
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    if exc.failure_type == FailureType.INVALID_URL:
        status_code = status.HTTP_400_BAD_REQUEST
    elif exc.failure_type == FailureType.PLAYLIST_UNAVAILABLE:
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.failure_kind == FailureKind.TRANSIENT:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif exc.failure_kind == FailureKind.PERMANENT:
        status_code = status.HTTP_400_BAD_REQUEST

    return JSONResponse(
        status_code=status_code,
        content=error_envelope(
            exc.failure_type.value.upper(),
            exc.reason,
            {
                "failure_type": exc.failure_type.value,
                "failure_kind": exc.failure_kind.value,
                "diagnostic": exc.diagnostic,
            },
        ),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers with the FastAPI app."""
    app.add_exception_handler(PipelineError, pipeline_error_handler)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(HTTPError, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    # Registered before the catch-all so framework 404/405 use the same envelope.
    app.add_exception_handler(StarletteHTTPException, starlette_http_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)

