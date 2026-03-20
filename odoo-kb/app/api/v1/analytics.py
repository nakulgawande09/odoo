"""Analytics endpoint: query stats, cache performance, system health."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.auth import verify_api_key
from app.dependencies import get_query_logger, get_cache_stats

router = APIRouter()


@router.get("/analytics")
async def get_analytics(
    _api_key: str | None = Depends(verify_api_key),
) -> dict[str, Any]:
    """Return query analytics and cache statistics."""
    query_logger = get_query_logger()
    cache_stats = get_cache_stats()

    result: dict[str, Any] = {}

    if query_logger:
        result["queries"] = query_logger.get_analytics()

    if cache_stats:
        result["cache"] = cache_stats

    if not result:
        result["message"] = "No analytics data available"

    return result


@router.get("/analytics/recent-queries")
async def get_recent_queries(
    limit: int = 50,
    _api_key: str | None = Depends(verify_api_key),
) -> list[dict]:
    """Return the most recent queries."""
    query_logger = get_query_logger()
    if query_logger is None:
        return []
    return query_logger.get_recent_queries(limit=limit)
