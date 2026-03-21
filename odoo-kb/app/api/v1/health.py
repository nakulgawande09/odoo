"""Health check endpoint with component-level status."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.dependencies import get_backend, get_cache_stats

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check(backend=Depends(get_backend)) -> dict:
    """Check service and component health.

    Returns component-level status so load balancers and monitoring
    systems can make informed routing decisions.
    """
    components: dict[str, dict] = {}

    # Backend (database)
    try:
        backend_ok = await backend.health_check()
        components["backend"] = {
            "name": backend.name,
            "healthy": backend_ok,
        }
    except Exception as exc:
        components["backend"] = {
            "name": getattr(backend, "name", "unknown"),
            "healthy": False,
            "error": str(exc),
        }

    # Redis (if configured)
    try:
        from app.core.redis_client import get_redis
        redis = get_redis()
        await redis.ping()
        components["redis"] = {"healthy": True}
    except (RuntimeError, Exception):
        # RuntimeError = not initialized (memory mode), skip
        pass

    # Cache stats
    cache = get_cache_stats()
    if cache:
        components["cache"] = {
            "healthy": True,
            **cache,
        }

    # Overall status: degraded if any component is unhealthy
    all_healthy = all(
        c.get("healthy", True)
        for c in components.values()
    )

    return {
        "status": "healthy" if all_healthy else "degraded",
        "components": components,
    }
