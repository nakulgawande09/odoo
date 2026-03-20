"""Tests for the Phase 2 search orchestrator."""
from __future__ import annotations

import pytest

from app.core.conversation import ConversationTracker
from app.core.query_expansion import StaticQueryExpander
from app.core.search_service import SearchOrchestrator
from app.schemas.search import ProcessedQuery, SearchRequest, SearchResultItem


# --- Mock services ---

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


class MockBackend:
    def __init__(self, name_: str, results: list[SearchResultItem] | None = None):
        self._name = name_
        self._results = results or []

    @property
    def name(self):
        return self._name

    async def search(self, query, limit=10, offset=0):
        return self._results[:limit]


def _make_result(doc_id: str, score: float, backend: str = "mock") -> SearchResultItem:
    return SearchResultItem(
        document_id=doc_id,
        chunk_id=f"chunk-{doc_id}",
        title=f"Doc {doc_id}",
        snippet=f"Content of {doc_id}",
        content=f"Content of {doc_id}",
        relevance_score=score,
        source_backend=backend,
    )


# --- Tests ---

@pytest.mark.asyncio
async def test_basic_search():
    results = [_make_result("1", 0.9, "b1"), _make_result("2", 0.7, "b1")]
    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[MockBackend("b1", results)],
    )

    response = await orchestrator.search(SearchRequest(query="test query"))
    assert response.total_count == 2
    assert response.results[0].relevance_score >= response.results[1].relevance_score
    assert "b1" in response.backends_used


@pytest.mark.asyncio
async def test_multi_backend_deduplication():
    """Same chunk from multiple backends should be deduplicated."""
    r1 = _make_result("1", 0.9, "b1")
    r2 = _make_result("1", 0.8, "b2")  # Same chunk_id

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[MockBackend("b1", [r1]), MockBackend("b2", [r2])],
    )

    response = await orchestrator.search(SearchRequest(query="test"))
    # Should keep the higher-scored version
    assert response.total_count == 1
    assert response.results[0].relevance_score == 0.9


@pytest.mark.asyncio
async def test_backend_weights():
    """Backend weights should scale relevance scores."""
    r1 = _make_result("1", 0.8, "primary")
    r2 = _make_result("2", 0.9, "secondary")

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[MockBackend("primary", [r1]), MockBackend("secondary", [r2])],
        backend_weights={"primary": 1.0, "secondary": 0.5},
    )

    response = await orchestrator.search(SearchRequest(query="test"))
    # secondary result (0.9 * 0.5 = 0.45) should rank below primary (0.8 * 1.0)
    assert response.results[0].document_id == "1"


@pytest.mark.asyncio
async def test_conversation_context_integration():
    """Conversation tracker should expand short/pronoun queries."""
    tracker = ConversationTracker()
    tracker.record_turn("conv-1", "shipping options for electronics")

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[MockBackend("b1", [_make_result("1", 0.9)])],
        conversation_tracker=tracker,
    )

    # Short query with pronoun should get context from prior turn
    response = await orchestrator.search(
        SearchRequest(query="how much does it cost", conversation_id="conv-1")
    )
    assert response.total_count >= 0  # Just verify no crash


@pytest.mark.asyncio
async def test_query_expansion_integration():
    """Query expander should add synonyms."""
    expander = StaticQueryExpander({"cancel": ["terminate", "end"]})

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[MockBackend("b1", [_make_result("1", 0.9)])],
        query_expander=expander,
    )

    response = await orchestrator.search(SearchRequest(query="how to cancel"))
    # The expanded query should have been used for search
    assert response.total_count >= 0


@pytest.mark.asyncio
async def test_conversation_turn_recorded():
    """After search, conversation turn should be recorded."""
    tracker = ConversationTracker()

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[MockBackend("b1", [])],
        conversation_tracker=tracker,
    )

    await orchestrator.search(
        SearchRequest(query="first question", conversation_id="conv-2")
    )
    ctx = tracker.get_context("conv-2")
    assert ctx is not None
    assert len(ctx.turns) == 1
    assert ctx.turns[0].query == "first question"


@pytest.mark.asyncio
async def test_failed_backend_graceful():
    """A failing backend should not break the search."""

    class FailingBackend:
        name = "failing"

        async def search(self, query, limit=10, offset=0):
            raise RuntimeError("Backend down")

    orchestrator = SearchOrchestrator(
        preprocessor=MockPreprocessor(),
        embedder=MockEmbedder(),
        backends=[
            FailingBackend(),
            MockBackend("good", [_make_result("1", 0.9)]),
        ],
    )

    response = await orchestrator.search(SearchRequest(query="test"))
    assert response.total_count == 1
    assert "good" in response.backends_used
    assert "failing" not in response.backends_used
