"""Tests for query logger."""
from __future__ import annotations

import pytest

from app.core.query_logger import QueryLogger


@pytest.fixture
def logger_instance():
    return QueryLogger(session_factory=None)


@pytest.mark.asyncio
async def test_log_increments_counts(logger_instance):
    await logger_instance.log("test query", results_count=5, search_time_ms=10.0)
    await logger_instance.log("test query", results_count=3, search_time_ms=20.0)
    await logger_instance.log("other query", results_count=1, search_time_ms=5.0)

    analytics = logger_instance.get_analytics()
    assert analytics["total_queries"] == 3
    assert analytics["top_queries"][0] == ("test query", 2)


@pytest.mark.asyncio
async def test_log_tracks_intents(logger_instance):
    await logger_instance.log("q1", processed_query={"intent": "how_to"})
    await logger_instance.log("q2", processed_query={"intent": "how_to"})
    await logger_instance.log("q3", processed_query={"intent": "troubleshooting"})

    analytics = logger_instance.get_analytics()
    assert analytics["intent_distribution"]["how_to"] == 2
    assert analytics["intent_distribution"]["troubleshooting"] == 1


@pytest.mark.asyncio
async def test_log_tracks_sources(logger_instance):
    await logger_instance.log("q1", source="livechat")
    await logger_instance.log("q2", source="livechat")
    await logger_instance.log("q3", source="chatbot")

    analytics = logger_instance.get_analytics()
    assert analytics["source_distribution"]["livechat"] == 2


@pytest.mark.asyncio
async def test_average_search_time(logger_instance):
    await logger_instance.log("q1", search_time_ms=10.0)
    await logger_instance.log("q2", search_time_ms=20.0)

    analytics = logger_instance.get_analytics()
    assert analytics["avg_search_time_ms"] == 15.0


@pytest.mark.asyncio
async def test_recent_queries(logger_instance):
    await logger_instance.log("first query")
    await logger_instance.log("second query")

    recent = logger_instance.get_recent_queries(limit=10)
    assert len(recent) == 2
    # Most recent first
    assert recent[0]["query"] == "second query"
    assert recent[1]["query"] == "first query"


@pytest.mark.asyncio
async def test_empty_analytics():
    ql = QueryLogger()
    analytics = ql.get_analytics()
    assert analytics["total_queries"] == 0
    assert analytics["avg_search_time_ms"] == 0.0


@pytest.mark.asyncio
async def test_log_never_raises(logger_instance):
    """Logger should swallow errors gracefully."""
    # Even with None processed_query, should not raise
    await logger_instance.log("q", processed_query=None, results_count=0)
    assert logger_instance.get_analytics()["total_queries"] == 1
