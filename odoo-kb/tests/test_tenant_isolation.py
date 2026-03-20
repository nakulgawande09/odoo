"""Tests for tenant isolation in search pipeline."""
from __future__ import annotations

import pytest

from app.core.search_service import SearchOrchestrator
from app.schemas.search import ProcessedQuery, SearchRequest, SearchResultItem


class MockPreprocessor:
    async def process(self, raw_query, context=None):
        return ProcessedQuery(
            original_query=raw_query,
            intent="informational",
            search_text=raw_query,
            filters={},
            confidence=0.5,
        )


class MockEmbedder:
    dimensions = 4

    async def embed(self, text):
        return [0.1, 0.2, 0.3, 0.4]

    async def embed_batch(self, texts):
        return [[0.1, 0.2, 0.3, 0.4]] * len(texts)


class TenantAwareBackend:
    """Mock backend that filters by tenant_id."""

    def __init__(self, results: list[SearchResultItem]):
        self._all_results = results

    @property
    def name(self):
        return "tenant_mock"

    async def search(self, query: ProcessedQuery, limit=10, offset=0):
        # Simulate tenant filtering
        if query.tenant_id:
            return [
                r for r in self._all_results
                if r.metadata.get("tenant_id") == query.tenant_id
                or r.metadata.get("tenant_id") is None
            ][:limit]
        return self._all_results[:limit]


def _make_result(doc_id: str, score: float, tenant_id: str | None = None) -> SearchResultItem:
    return SearchResultItem(
        document_id=doc_id,
        chunk_id=f"chunk-{doc_id}",
        title=f"Doc {doc_id}",
        snippet=f"Content of {doc_id}",
        content=f"Content of {doc_id}",
        relevance_score=score,
        source_backend="tenant_mock",
        metadata={"tenant_id": tenant_id},
    )


@pytest.mark.asyncio
async def test_tenant_id_passed_to_backend():
    """Search request tenant_id should be propagated to ProcessedQuery."""
    results = [
        _make_result("1", 0.9, "tenant-A"),
        _make_result("2", 0.8, "tenant-B"),
        _make_result("3", 0.7, None),  # shared doc
    ]

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[TenantAwareBackend(results)],
    )

    # Search with tenant-A: should get tenant-A docs + shared
    response = await orchestrator.search(
        SearchRequest(query="test", tenant_id="tenant-A")
    )
    doc_ids = {r.document_id for r in response.results}
    assert "1" in doc_ids  # tenant-A
    assert "3" in doc_ids  # shared
    assert "2" not in doc_ids  # tenant-B excluded


@pytest.mark.asyncio
async def test_no_tenant_returns_all():
    """Without tenant_id, all documents should be returned."""
    results = [
        _make_result("1", 0.9, "tenant-A"),
        _make_result("2", 0.8, "tenant-B"),
    ]

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[TenantAwareBackend(results)],
    )

    response = await orchestrator.search(SearchRequest(query="test"))
    assert response.total_count == 2


@pytest.mark.asyncio
async def test_search_request_has_tenant_id():
    """SearchRequest should accept tenant_id."""
    req = SearchRequest(query="test", tenant_id="my-tenant")
    assert req.tenant_id == "my-tenant"

    req2 = SearchRequest(query="test")
    assert req2.tenant_id is None
