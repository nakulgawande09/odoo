"""Tests for GeminiEmbedder."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import EmbeddingError
from app.ingestion.embedder import GeminiEmbedder, create_embedder


def _make_settings(**overrides):
    defaults = {
        "embedding_provider": "gemini",
        "embedding_model": "models/text-embedding-004",
        "embedding_dimensions": 768,
        "gemini_api_key": "test-key",
        "openai_api_key": "",
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_gemini_embedder_dimensions():
    embedder = GeminiEmbedder(_make_settings())
    assert embedder.dimensions == 768


def test_create_embedder_gemini():
    embedder = create_embedder(_make_settings())
    assert isinstance(embedder, GeminiEmbedder)


def test_create_embedder_unknown_raises():
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        create_embedder(_make_settings(embedding_provider="unknown"))


@pytest.mark.asyncio
async def test_embed_batch_empty():
    embedder = GeminiEmbedder(_make_settings())
    result = await embedder.embed_batch([])
    assert result == []


@pytest.mark.asyncio
async def test_embed_delegates_to_embed_batch():
    embedder = GeminiEmbedder(_make_settings())

    mock_embedding = MagicMock()
    mock_embedding.values = [0.1, 0.2, 0.3]

    mock_response = MagicMock()
    mock_response.embeddings = [mock_embedding]

    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = mock_response
    embedder._client = mock_client

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=mock_response
        )
        result = await embedder.embed("test text")

    assert result == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_batch_returns_values():
    embedder = GeminiEmbedder(_make_settings())

    emb1 = MagicMock()
    emb1.values = [0.1, 0.2]
    emb2 = MagicMock()
    emb2.values = [0.3, 0.4]

    mock_response = MagicMock()
    mock_response.embeddings = [emb1, emb2]

    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = mock_response
    embedder._client = mock_client

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=mock_response
        )
        result = await embedder.embed_batch(["text1", "text2"])

    assert len(result) == 2
    assert result[0] == [0.1, 0.2]
    assert result[1] == [0.3, 0.4]


@pytest.mark.asyncio
async def test_embed_batch_wraps_errors():
    embedder = GeminiEmbedder(_make_settings())

    mock_client = MagicMock()
    embedder._client = mock_client

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(
            side_effect=RuntimeError("API error")
        )
        with pytest.raises(EmbeddingError, match="Gemini embedding failed"):
            await embedder.embed_batch(["test"])
