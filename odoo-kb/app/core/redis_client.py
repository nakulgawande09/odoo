"""Shared async Redis connection pool.

Used by Redis-backed caches, voice agent store, and conversation tracker.
All Redis access in the application should go through the pool returned
by ``get_redis()`` to share connections efficiently.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_pool: Any = None


async def init_redis(redis_url: str = "redis://localhost:6379/0") -> Any:
    """Create and return an async Redis connection pool."""
    global _pool
    from redis.asyncio import Redis

    _pool = Redis.from_url(
        redis_url,
        decode_responses=True,
        max_connections=20,
    )
    # Verify connectivity
    await _pool.ping()
    logger.info("Redis connected: %s", redis_url)
    return _pool


async def close_redis() -> None:
    """Close the Redis connection pool."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
        logger.info("Redis connection closed")


def get_redis() -> Any:
    """Return the shared Redis instance. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Redis not initialized — call init_redis() first")
    return _pool
