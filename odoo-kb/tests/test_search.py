"""Tests for query preprocessing and search schemas."""
from __future__ import annotations

import pytest

from app.schemas.search import SearchRequest, SearchResponse, SearchResultItem


def test_search_request_defaults():
    req = SearchRequest(query="hello world")
    assert req.limit == 10
    assert req.offset == 0
    assert req.source == "generic"
    assert req.filters is None


def test_search_request_validation():
    with pytest.raises(Exception):
        SearchRequest(query="")  # min_length=1


def test_search_result_item():
    item = SearchResultItem(
        document_id="doc1",
        chunk_id="chunk1",
        title="Test",
        snippet="A test snippet",
        relevance_score=0.85,
        source_backend="pgvector",
    )
    assert item.relevance_score == 0.85
    assert item.tags == []
    assert item.connected_entities == []


def test_search_response():
    resp = SearchResponse(
        query="test",
        results=[],
        total_count=0,
        search_time_ms=1.5,
        backends_used=["pgvector"],
    )
    assert resp.parsed_intent is None
    assert resp.parsed_filters == {}
