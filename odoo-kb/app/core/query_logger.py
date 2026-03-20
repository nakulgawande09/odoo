"""Query logger: records every search to kb_query_log for analytics.

Runs as a fire-and-forget background task so it never slows down
the search response. If logging fails, it logs a warning but
doesn't affect the caller.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class QueryLogger:
    """Logs search queries to the database and tracks in-memory analytics.

    Dual-write: persists to QueryLogRecord (if a DB session factory is
    available) AND keeps an in-memory rolling window for fast analytics.
    """

    def __init__(self, session_factory: Any = None, window_size: int = 10_000) -> None:
        self._session_factory = session_factory
        self._window_size = window_size
        # In-memory analytics
        self._query_counts: Counter = Counter()
        self._intent_counts: Counter = Counter()
        self._source_counts: Counter = Counter()
        self._recent_queries: list[dict] = []
        self._total_queries: int = 0
        self._total_search_time_ms: float = 0.0

    async def log(
        self,
        query: str,
        processed_query: dict | None = None,
        results_count: int = 0,
        source: str = "generic",
        conversation_id: str | None = None,
        search_time_ms: float = 0.0,
        tenant_id: str | None = None,
    ) -> None:
        """Record a search query. Best-effort, never raises."""
        try:
            # Update in-memory analytics
            self._total_queries += 1
            self._total_search_time_ms += search_time_ms
            self._query_counts[query.lower().strip()] += 1
            if processed_query and processed_query.get("intent"):
                self._intent_counts[processed_query["intent"]] += 1
            self._source_counts[source] += 1

            entry = {
                "query": query,
                "results_count": results_count,
                "source": source,
                "search_time_ms": search_time_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._recent_queries.append(entry)
            if len(self._recent_queries) > self._window_size:
                self._recent_queries = self._recent_queries[-self._window_size:]

            # Persist to database if available
            if self._session_factory:
                await self._persist(
                    query=query,
                    processed_query=processed_query or {},
                    results_count=results_count,
                    source=source,
                    conversation_id=conversation_id,
                    search_time_ms=search_time_ms,
                )
        except Exception as e:
            logger.warning("Failed to log query: %s", e)

    async def _persist(self, **kwargs: Any) -> None:
        """Write to kb_query_log table."""
        from app.models.document import QueryLogRecord

        async with self._session_factory() as session:
            async with session.begin():
                record = QueryLogRecord(
                    id=str(uuid.uuid4()),
                    query=kwargs["query"],
                    processed_query=kwargs.get("processed_query", {}),
                    results_count=kwargs.get("results_count", 0),
                    source=kwargs.get("source", "generic"),
                    conversation_id=kwargs.get("conversation_id"),
                    search_time_ms=int(kwargs.get("search_time_ms", 0)),
                )
                session.add(record)

    def get_analytics(self) -> dict[str, Any]:
        """Return current analytics snapshot."""
        avg_time = (
            self._total_search_time_ms / self._total_queries
            if self._total_queries > 0
            else 0.0
        )
        return {
            "total_queries": self._total_queries,
            "avg_search_time_ms": round(avg_time, 2),
            "top_queries": self._query_counts.most_common(20),
            "intent_distribution": dict(self._intent_counts.most_common(10)),
            "source_distribution": dict(self._source_counts.most_common(10)),
            "recent_queries_count": len(self._recent_queries),
        }

    def get_recent_queries(self, limit: int = 50) -> list[dict]:
        """Return the most recent queries."""
        return self._recent_queries[-limit:][::-1]
