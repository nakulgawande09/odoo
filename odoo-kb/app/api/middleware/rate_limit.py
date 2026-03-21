"""Rate limiting middleware using a sliding window counter.

Supports both Redis-backed (for multi-instance) and in-memory (for dev)
rate limiting. Limits are per API key (or per IP if no key).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    """Simple sliding window rate limiter backed by a dict."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """Check if a request is allowed. Returns (allowed, remaining)."""
        now = time.monotonic()
        cutoff = now - self._window

        # Prune old entries
        entries = self._requests[key]
        self._requests[key] = entries = [t for t in entries if t > cutoff]

        if len(entries) >= self._max:
            return False, 0

        entries.append(now)
        return True, self._max - len(entries)


class RedisRateLimiter:
    """Sliding window rate limiter using Redis sorted sets."""

    def __init__(self, redis, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._r = redis
        self._max = max_requests
        self._window = window_seconds
        self._prefix = "kb:ratelimit:"

    async def is_allowed(self, key: str) -> tuple[bool, int]:
        import time as _time

        redis_key = f"{self._prefix}{key}"
        now = _time.time()
        cutoff = now - self._window

        pipe = self._r.pipeline()
        pipe.zremrangebyscore(redis_key, 0, cutoff)
        pipe.zcard(redis_key)
        pipe.zadd(redis_key, {str(now): now})
        pipe.expire(redis_key, self._window + 1)
        results = await pipe.execute()

        current_count = results[1]  # zcard result (before our zadd)
        if current_count >= self._max:
            # Remove the zadd we just did
            await self._r.zrem(redis_key, str(now))
            return False, 0

        return True, self._max - current_count - 1


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces per-key rate limits."""

    # Paths exempt from rate limiting
    EXEMPT_PATHS = {"/v1/health", "/docs", "/openapi.json", "/redoc"}

    def __init__(self, app, limiter, burst_limit: int = 0) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._burst = burst_limit

    def _get_key(self, request: Request) -> str:
        """Extract rate limit key from API key or client IP."""
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return f"key:{auth[7:][:16]}"  # Use first 16 chars of key
        # Fall back to client IP
        client = request.client
        return f"ip:{client.host}" if client else "ip:unknown"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        key = self._get_key(request)

        # Check rate limit (handle both sync and async limiters)
        if hasattr(self._limiter, "is_allowed") and not hasattr(self._limiter.is_allowed, "__self__"):
            # It's a regular method (InMemoryRateLimiter)
            allowed, remaining = self._limiter.is_allowed(key)
        else:
            result = self._limiter.is_allowed(key)
            if hasattr(result, "__await__"):
                allowed, remaining = await result
            else:
                allowed, remaining = result

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Too many requests. Please slow down.",
                    }
                },
                headers={"Retry-After": "60"},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
