from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware that adds a correlation ID to each request.

    The correlation ID is:
    - Extracted from X-Correlation-ID header if present
    - Generated as UUID if not present
    - Added to response headers as X-Correlation-ID
    - Available in request.state.correlation_id
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        # Add to request logging context
        logger.info(
            "Request started",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            },
        )

        response = await call_next(request)

        response.headers["X-Correlation-ID"] = correlation_id

        logger.info(
            "Request completed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request/response details with timing."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()

        # Log request
        logger.debug(
            "Incoming request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query_params": dict(request.query_params),
            },
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "Request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "error": str(exc),
                },
            )
            raise

        process_time = time.perf_counter() - start_time

        logger.debug(
            "Request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "process_time_ms": round(process_time * 1000, 2),
            },
        )

        response.headers["X-Process-Time"] = str(process_time)
        return response