"""Integration tests for the KB service API.

These tests run against the real FastAPI app using httpx/TestClient.
They do NOT require a database — they mock the backend to test the
full HTTP request/response cycle including middleware, auth, and routing.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.schemas.document import DocumentStatus


@pytest.fixture
def mock_backend():
    """Create a mock backend that satisfies all backend interfaces."""
    backend = AsyncMock()
    backend.name = "mock"
    backend.health_check = AsyncMock(return_value=True)
    backend.search = AsyncMock(return_value=[])
    backend.list_documents = AsyncMock(return_value=[])
    backend.get_document = AsyncMock(return_value=None)
    backend.get_document_chunks = AsyncMock(return_value=[])
    backend.delete_document = AsyncMock()
    backend.delete_document_chunks = AsyncMock()
    backend.create_document = AsyncMock()
    backend.update_document_status = AsyncMock()
    backend.index_chunks = AsyncMock()
    return backend


@pytest.fixture
def mock_embedder():
    embedder = AsyncMock()
    embedder.dimensions = 1536
    embedder.embed = AsyncMock(return_value=[0.1] * 1536)
    embedder.embed_batch = AsyncMock(return_value=[[0.1] * 1536])
    embedder.cache_stats = {"hits": 0, "misses": 0}
    return embedder


@pytest.fixture
def app(mock_backend, mock_embedder):
    """Create a test app with mocked services."""
    from app.dependencies import set_services
    from app.core.cache import EmbeddingCache, SearchCache, CachedEmbedder
    from app.core.query_preprocessor import create_preprocessor
    from app.core.conversation import ConversationTracker
    from app.core.query_logger import QueryLogger
    from app.core.search_service import SearchOrchestrator
    from app.core.voice_agent_config import VoiceAgentStore
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.chunker import RecursiveChunker
    from config.settings import Settings

    # Create real services with mock backend
    settings = Settings(
        search_backend="pgvector",
        embedding_provider="openai",
    )
    embedding_cache = EmbeddingCache()
    search_cache = SearchCache()
    embedder = CachedEmbedder(mock_embedder, embedding_cache)
    preprocessor = create_preprocessor(settings, {})
    conversation_tracker = ConversationTracker()
    query_logger = QueryLogger()

    orchestrator = SearchOrchestrator(
        preprocessor=preprocessor,
        embedder=embedder,
        backends=[mock_backend],
        conversation_tracker=conversation_tracker,
        query_logger=query_logger,
        search_cache=search_cache,
    )

    pipeline = IngestionPipeline(
        backend=mock_backend,
        embedder=embedder,
        chunker=RecursiveChunker(),
    )

    set_services(
        backend=mock_backend,
        embedder=embedder,
        preprocessor=preprocessor,
        orchestrator=orchestrator,
        pipeline=pipeline,
        query_logger=query_logger,
        cache_stats_fn=lambda: {"embedding_cache": embedding_cache.stats, "search_cache": search_cache.stats},
        voice_agent_store=VoiceAgentStore(),
    )

    from app.main import create_app
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─── Health ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("healthy", "degraded")
    assert "components" in data


# ─── Search ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_endpoint(client):
    resp = await client.post("/v1/search", json={"query": "test query"})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "query" in data
    assert "search_time_ms" in data


@pytest.mark.asyncio
async def test_search_empty_query_rejected(client):
    resp = await client.post("/v1/search", json={"query": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_with_filters(client):
    resp = await client.post("/v1/search", json={
        "query": "return policy",
        "filters": {"category": "policy"},
        "limit": 3,
    })
    assert resp.status_code == 200


# ─── Documents ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_documents(client):
    resp = await client.get("/v1/documents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_document_not_found(client):
    resp = await client.get("/v1/documents/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ingest_document(client):
    resp = await client.post("/v1/documents", json={
        "content": "This is a test document for integration testing.",
        "title": "Test Doc",
        "tags": ["test"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Test Doc"
    assert data["status"] in ("indexed", "pending")


@pytest.mark.asyncio
async def test_async_ingest(client, mock_backend):
    resp = await client.post("/v1/documents/async", json={
        "content": "Background document content.",
        "title": "Async Doc",
    })
    assert resp.status_code == 202
    data = resp.json()
    assert "document_id" in data
    assert data["status"] == "pending"
    # Verify document record was created
    mock_backend.create_document.assert_called()


# ─── Metrics ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_endpoint(client):
    # Make a request first so there's data
    await client.get("/v1/health")
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "kb_http_requests_total" in resp.text


# ─── VOIP ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voip_query_endpoint(client):
    resp = await client.post("/v1/voip/query", json={
        "query": "What is the return policy?",
        "provider": "manual",
        "caller_number": "+1234567890",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "call_id" in data


@pytest.mark.asyncio
async def test_voip_agent_crud(client):
    # Create
    resp = await client.put("/v1/voip/agents/1", json={
        "name": "Test Agent",
        "provider": "twilio",
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Agent"

    # List
    resp = await client.get("/v1/voip/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert any(a["name"] == "Test Agent" for a in agents)

    # Delete
    resp = await client.delete("/v1/voip/agents/1")
    assert resp.status_code == 200


# ─── Analytics ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_endpoint(client):
    resp = await client.get("/v1/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert "queries" in data or "total_queries" in data


# ─── Error Handling ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_404_returns_json(client):
    resp = await client.get("/v1/nonexistent")
    assert resp.status_code in (404, 405)


# ─── WebSocket ───────────────────────────────────────────────

def test_websocket_search(mock_backend, mock_embedder):
    """Test WebSocket search endpoint using a minimal app without lifespan."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.v1.ws import router as ws_router
    from app.dependencies import set_services
    from app.core.cache import EmbeddingCache, SearchCache, CachedEmbedder
    from app.core.query_preprocessor import create_preprocessor
    from app.core.conversation import ConversationTracker
    from app.core.query_logger import QueryLogger
    from app.core.search_service import SearchOrchestrator
    from app.core.voice_agent_config import VoiceAgentStore
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.chunker import RecursiveChunker
    from config.settings import Settings

    settings = Settings(search_backend="pgvector", embedding_provider="openai")
    embedding_cache = EmbeddingCache()
    search_cache = SearchCache()
    embedder = CachedEmbedder(mock_embedder, embedding_cache)
    preprocessor = create_preprocessor(settings, {})

    orchestrator = SearchOrchestrator(
        preprocessor=preprocessor,
        embedder=embedder,
        backends=[mock_backend],
        conversation_tracker=ConversationTracker(),
        query_logger=QueryLogger(),
        search_cache=search_cache,
    )

    set_services(
        backend=mock_backend, embedder=embedder,
        preprocessor=preprocessor, orchestrator=orchestrator,
        pipeline=IngestionPipeline(backend=mock_backend, embedder=embedder, chunker=RecursiveChunker()),
        voice_agent_store=VoiceAgentStore(),
    )

    # Minimal app with just the WS router (no lifespan = no DB connection)
    ws_app = FastAPI()
    ws_app.include_router(ws_router, prefix="/v1")

    with TestClient(ws_app) as tc:
        with tc.websocket_connect("/v1/ws/search") as ws:
            ws.send_json({"query": "test search", "limit": 3})
            data = ws.receive_json()
            assert data["type"] == "results"
            assert "data" in data
            assert data["data"]["total_count"] == 0
