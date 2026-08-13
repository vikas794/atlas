from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from src.domain.exceptions import DomainError, ProviderError, CacheError, StorageError


logger = logging.getLogger(__name__)


class HTTPError(Exception):
    """Base HTTP error with status code and detail."""

    def __init__(self, status_code: int, detail: str, error_code: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        super().__init__(detail)


def domain_error_to_http(exc: DomainError) -> HTTPError:
    """Map domain errors to HTTP errors."""
    if isinstance(exc, ProviderError):
        return HTTPError(
            status_code=502,
            detail=str(exc),
            error_code=f"PROVIDER_ERROR_{exc.provider.upper() if exc.provider else 'UNKNOWN'}",
        )
    elif isinstance(exc, CacheError):
        return HTTPError(
            status_code=503,
            detail="Cache service temporarily unavailable",
            error_code="CACHE_ERROR",
        )
    elif isinstance(exc, StorageError):
        return HTTPError(
            status_code=500,
            detail="Storage operation failed",
            error_code="STORAGE_ERROR",
        )
    else:
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
        exc_info=True,
    )
    return JSONResponse(
        status_code=http_error.status_code,
        content={
            "error": http_error.error_code,
            "message": http_error.detail,
        },
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
        content={
            "error": exc.error_code or "HTTP_ERROR",
            "message": exc.detail,
        },
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
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": exc.errors(),
        },
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
        content={
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers with the FastAPI app."""
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(HTTPError, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)