"""Global exception handler for consistent error responses."""
from __future__ import annotations

import logging
import time
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


class KBError(Exception):
    """Base exception for Knowledge Base service errors."""

    def __init__(self, message: str, code: str = "internal_error", status_code: int = 500) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(KBError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, code="not_found", status_code=404)


class ValidationError(KBError):
    def __init__(self, message: str = "Invalid request") -> None:
        super().__init__(message, code="validation_error", status_code=422)


class ServiceUnavailableError(KBError):
    def __init__(self, message: str = "Service temporarily unavailable") -> None:
        super().__init__(message, code="service_unavailable", status_code=503)


class RateLimitError(KBError):
    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__(message, code="rate_limit_exceeded", status_code=429)


def register_error_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(KBError)
    async def kb_error_handler(request: Request, exc: KBError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled exception on %s %s: %s",
            request.method, request.url.path, exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An internal error occurred.",
                }
            },
        )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs request method, path, status code, and duration."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        # Skip health checks from access logging
        if request.url.path != "/v1/health":
            logger.info(
                "%s %s %d %.1fms",
                request.method, request.url.path,
                response.status_code, duration_ms,
            )
        return response
