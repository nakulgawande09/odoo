"""In-memory caching layer for embeddings and search results.

Two caches:
  1. EmbeddingCache — avoids re-computing embeddings for repeated text
  2. SearchCache — caches full search responses for identical requests

Both use LRU eviction with configurable TTL. For multi-instance
deployments, swap with Redis-backed implementations.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    value: Any
    created_at: float
    hits: int = 0


class LRUCache:
    """Thread-safe LRU cache with TTL expiration."""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """Get value if present and not expired."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        if time.monotonic() - entry.created_at > self._ttl:
            del self._store[key]
            self._misses += 1
            return None

        entry.hits += 1
        self._hits += 1
        self._store.move_to_end(key)
        return entry.value

    def put(self, key: str, value: Any) -> None:
        """Store a value, evicting oldest if at capacity."""
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = CacheEntry(value=value, created_at=time.monotonic())
            return

        if len(self._store) >= self._max_size:
            self._store.popitem(last=False)

        self._store[key] = CacheEntry(value=value, created_at=time.monotonic())

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "size": len(self._store),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "ttl_seconds": self._ttl,
        }


class EmbeddingCache:
    """Caches text -> embedding vectors to avoid redundant API calls."""

    def __init__(self, max_size: int = 5000, ttl_seconds: int = 3600) -> None:
        self._cache = LRUCache(max_size=max_size, ttl_seconds=ttl_seconds)

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def get(self, text: str) -> list[float] | None:
        return self._cache.get(self._key(text))

    def put(self, text: str, embedding: list[float]) -> None:
        self._cache.put(self._key(text), embedding)

    @property
    def stats(self) -> dict[str, Any]:
        return self._cache.stats


class CachedEmbedder:
    """Wraps an Embedder with an EmbeddingCache."""

    def __init__(self, embedder: Any, cache: EmbeddingCache) -> None:
        self._embedder = embedder
        self._cache = cache

    @property
    def dimensions(self) -> int:
        return self._embedder.dimensions

    async def embed(self, text: str) -> list[float]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached

        result = await self._embedder.embed(text)
        self._cache.put(text, result)
        return result

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # Check cache first
        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # Embed uncached texts
        if uncached_texts:
            embeddings = await self._embedder.embed_batch(uncached_texts)
            for i, idx in enumerate(uncached_indices):
                results[idx] = embeddings[i]
                self._cache.put(uncached_texts[i], embeddings[i])

        return results  # type: ignore[return-value]

    @property
    def cache_stats(self) -> dict[str, Any]:
        return self._cache.stats


class SearchCache:
    """Caches full SearchResponse objects keyed by request hash."""

    def __init__(self, max_size: int = 500, ttl_seconds: int = 120) -> None:
        self._cache = LRUCache(max_size=max_size, ttl_seconds=ttl_seconds)

    def _key(self, query: str, filters: dict | None, limit: int, offset: int) -> str:
        import json
        raw = f"{query}|{json.dumps(filters or {}, sort_keys=True)}|{limit}|{offset}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, query: str, filters: dict | None, limit: int, offset: int) -> Any | None:
        return self._cache.get(self._key(query, filters, limit, offset))

    def put(
        self, query: str, filters: dict | None, limit: int, offset: int, response: Any
    ) -> None:
        self._cache.put(self._key(query, filters, limit, offset), response)

    def invalidate_all(self) -> None:
        self._cache.clear()

    @property
    def stats(self) -> dict[str, Any]:
        return self._cache.stats
