"""Tests for semantic reranker."""
from __future__ import annotations

import pytest

from app.core.reranker import CrossEncoderReranker, create_reranker
from app.schemas.search import SearchResultItem


class MockSettings:
    reranker_provider = "none"
    reranker_model = ""
    reranker_top_k = 20
    openai_api_key = ""


def _make_result(doc_id: str, score: float, content: str = "test") -> SearchResultItem:
    return SearchResultItem(
        document_id=doc_id,
        chunk_id=f"chunk-{doc_id}",
        title=f"Doc {doc_id}",
        snippet=content,
        content=content,
        relevance_score=score,
        source_backend="pgvector",
    )


def test_create_reranker_none():
    settings = MockSettings()
    assert create_reranker(settings) is None


def test_create_reranker_configured():
    settings = MockSettings()
    settings.reranker_provider = "openai"
    reranker = create_reranker(settings)
    assert reranker is not None
    assert isinstance(reranker, CrossEncoderReranker)


@pytest.mark.asyncio
async def test_reranker_passthrough_when_disabled():
    """When provider is 'none', results pass through unchanged."""
    settings = MockSettings()
    reranker = CrossEncoderReranker(settings)

    results = [_make_result("1", 0.9), _make_result("2", 0.5)]
    reranked = await reranker.rerank("test query", results)

    assert len(reranked) == 2
    assert reranked[0].document_id == "1"  # order preserved
    assert reranked[0].relevance_score == 0.9


@pytest.mark.asyncio
async def test_reranker_empty_results():
    settings = MockSettings()
    settings.reranker_provider = "openai"
    reranker = CrossEncoderReranker(settings)

    reranked = await reranker.rerank("test query", [])
    assert reranked == []


@pytest.mark.asyncio
async def test_reranker_preserves_valid_scores():
    """Reranker should preserve valid scores when provider is 'none'."""
    settings = MockSettings()
    reranker = CrossEncoderReranker(settings)

    result = _make_result("1", 0.95)
    reranked = await reranker.rerank("test", [result])
    assert len(reranked) == 1
    assert reranked[0].relevance_score == 0.95
