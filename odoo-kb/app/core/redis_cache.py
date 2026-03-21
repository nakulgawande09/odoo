"""Redis-backed cache implementations for multi-instance deployments.

Drop-in replacements for the in-memory LRUCache, EmbeddingCache, and
SearchCache classes. Selected via ``KB_CACHE_BACKEND=redis`` setting.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Key prefixes to namespace different cache types in Redis
_EMB_PREFIX = "kb:emb:"
_SEARCH_PREFIX = "kb:search:"


class RedisEmbeddingCache:
    """Caches text -> embedding vectors in Redis."""

    def __init__(self, redis: Any, ttl_seconds: int = 3600) -> None:
        self._r = redis
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def _key(self, text: str) -> str:
        return _EMB_PREFIX + hashlib.sha256(text.encode()).hexdigest()

    async def get(self, text: str) -> list[float] | None:
        raw = await self._r.get(self._key(text))
        if raw is None:
            self._misses += 1
            return None
        self._hits += 1
        return json.loads(raw)

    async def put(self, text: str, embedding: list[float]) -> None:
        await self._r.set(
            self._key(text),
            json.dumps(embedding),
            ex=self._ttl,
        )

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "backend": "redis",
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            "ttl_seconds": self._ttl,
        }


class RedisSearchCache:
    """Caches full SearchResponse objects in Redis."""

    def __init__(self, redis: Any, ttl_seconds: int = 120) -> None:
        self._r = redis
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def _key(self, query: str, filters: dict | None, limit: int, offset: int) -> str:
        raw = f"{query}|{json.dumps(filters or {}, sort_keys=True)}|{limit}|{offset}"
        return _SEARCH_PREFIX + hashlib.sha256(raw.encode()).hexdigest()

    async def get(self, query: str, filters: dict | None, limit: int, offset: int) -> Any | None:
        raw = await self._r.get(self._key(query, filters, limit, offset))
        if raw is None:
            self._misses += 1
            return None
        self._hits += 1
        return json.loads(raw)

    async def put(
        self, query: str, filters: dict | None, limit: int, offset: int, response: Any
    ) -> None:
        # Serialize Pydantic models or dicts
        if hasattr(response, "model_dump"):
            data = response.model_dump(mode="json")
        else:
            data = response
        await self._r.set(
            self._key(query, filters, limit, offset),
            json.dumps(data),
            ex=self._ttl,
        )

    async def invalidate_all(self) -> None:
        # Use SCAN to find and delete all search cache keys
        cursor = 0
        while True:
            cursor, keys = await self._r.scan(cursor, match=f"{_SEARCH_PREFIX}*", count=100)
            if keys:
                await self._r.delete(*keys)
            if cursor == 0:
                break

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "backend": "redis",
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            "ttl_seconds": self._ttl,
        }


class RedisCachedEmbedder:
    """Wraps an Embedder with a Redis-backed EmbeddingCache."""

    def __init__(self, embedder: Any, cache: RedisEmbeddingCache) -> None:
        self._embedder = embedder
        self._cache = cache

    @property
    def dimensions(self) -> int:
        return self._embedder.dimensions

    async def embed(self, text: str) -> list[float]:
        cached = await self._cache.get(text)
        if cached is not None:
            return cached
        result = await self._embedder.embed(text)
        await self._cache.put(text, result)
        return result

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            cached = await self._cache.get(text)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            embeddings = await self._embedder.embed_batch(uncached_texts)
            for i, idx in enumerate(uncached_indices):
                results[idx] = embeddings[i]
                await self._cache.put(uncached_texts[i], embeddings[i])

        return results  # type: ignore[return-value]

    @property
    def cache_stats(self) -> dict[str, Any]:
        return self._cache.stats
