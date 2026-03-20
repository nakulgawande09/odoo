"""Embedding generation adapters."""
from __future__ import annotations

import logging
from typing import Any

from app.core.exceptions import EmbeddingError

logger = logging.getLogger(__name__)


class OpenAIEmbedder:
    """Generates embeddings using OpenAI's API."""

    def __init__(self, settings: Any) -> None:
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._api_key = settings.openai_api_key
        self._client = None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self._api_key)
            except ImportError:
                raise EmbeddingError(
                    "openai package required. Install with: pip install openai"
                )
        return self._client

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        try:
            response = await client.embeddings.create(
                model=self._model,
                input=texts,
                dimensions=self._dimensions,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            raise EmbeddingError(f"Embedding generation failed: {e}") from e


class LocalEmbedder:
    """Generates embeddings using a local sentence-transformers model.

    Useful for development/testing without external API calls.
    """

    def __init__(self, settings: Any) -> None:
        self._model_name = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._model = None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
            except ImportError:
                raise EmbeddingError(
                    "sentence-transformers required. Install with: "
                    "pip install sentence-transformers"
                )
        return self._model

    async def embed(self, text: str) -> list[float]:
        model = self._get_model()
        embedding = model.encode(text)
        return embedding.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        embeddings = model.encode(texts)
        return [e.tolist() for e in embeddings]


def create_embedder(settings: Any) -> OpenAIEmbedder | LocalEmbedder:
    """Factory to create the configured embedder."""
    if settings.embedding_provider == "openai":
        return OpenAIEmbedder(settings)
    elif settings.embedding_provider == "local":
        return LocalEmbedder(settings)
    else:
        raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")
