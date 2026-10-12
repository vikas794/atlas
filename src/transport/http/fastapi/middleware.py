from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

#: A client-supplied correlation id is only trusted when it cannot inject log lines.
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """One log line per request, with a correlation id echoed in ``X-Correlation-ID``.

    Successful GETs are logged at DEBUG (the UI polls every few seconds); everything else at
    INFO. Unhandled exceptions are logged once, by the generic error handler, not here.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        supplied = request.headers.get("X-Correlation-ID", "")
        correlation_id = supplied if _CORRELATION_ID.fullmatch(supplied) else uuid.uuid4().hex
        request.state.correlation_id = correlation_id
        start = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Correlation-ID"] = correlation_id
        quiet = request.method in ("GET", "HEAD") and response.status_code < 400
        logger.log(
            logging.DEBUG if quiet else logging.INFO,
            "%s %s -> %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={"correlation_id": correlation_id, "duration_ms": elapsed_ms},
        )
        return response


__all__ = ["RequestLoggingMiddleware"]
