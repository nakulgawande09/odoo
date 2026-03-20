"""Tests for caching layer."""
from __future__ import annotations

import time

import pytest

from app.core.cache import (
    CachedEmbedder,
    EmbeddingCache,
    LRUCache,
    SearchCache,
)


class TestLRUCache:
    def test_put_and_get(self):
        cache = LRUCache(max_size=10, ttl_seconds=60)
        cache.put("k1", "v1")
        assert cache.get("k1") == "v1"

    def test_miss_returns_none(self):
        cache = LRUCache(max_size=10, ttl_seconds=60)
        assert cache.get("missing") is None

    def test_eviction_on_capacity(self):
        cache = LRUCache(max_size=2, ttl_seconds=60)
        cache.put("k1", "v1")
        cache.put("k2", "v2")
        cache.put("k3", "v3")  # should evict k1
        assert cache.get("k1") is None
        assert cache.get("k2") == "v2"
        assert cache.get("k3") == "v3"

    def test_ttl_expiration(self):
        cache = LRUCache(max_size=10, ttl_seconds=0)  # instant expiry
        cache.put("k1", "v1")
        # TTL=0 means expired immediately on next check
        assert cache.get("k1") is None

    def test_lru_order(self):
        cache = LRUCache(max_size=2, ttl_seconds=60)
        cache.put("k1", "v1")
        cache.put("k2", "v2")
        cache.get("k1")  # access k1 → k2 is now LRU
        cache.put("k3", "v3")  # should evict k2
        assert cache.get("k1") == "v1"
        assert cache.get("k2") is None

    def test_stats(self):
        cache = LRUCache(max_size=10, ttl_seconds=60)
        cache.put("k1", "v1")
        cache.get("k1")  # hit
        cache.get("k2")  # miss

        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert stats["hit_rate"] == 0.5

    def test_invalidate(self):
        cache = LRUCache(max_size=10, ttl_seconds=60)
        cache.put("k1", "v1")
        cache.invalidate("k1")
        assert cache.get("k1") is None

    def test_clear(self):
        cache = LRUCache(max_size=10, ttl_seconds=60)
        cache.put("k1", "v1")
        cache.put("k2", "v2")
        cache.clear()
        assert cache.stats["size"] == 0


class TestEmbeddingCache:
    def test_cache_hit(self):
        cache = EmbeddingCache(max_size=10, ttl_seconds=60)
        cache.put("hello", [0.1, 0.2, 0.3])
        assert cache.get("hello") == [0.1, 0.2, 0.3]

    def test_cache_miss(self):
        cache = EmbeddingCache(max_size=10, ttl_seconds=60)
        assert cache.get("missing") is None

    def test_same_text_same_key(self):
        cache = EmbeddingCache(max_size=10, ttl_seconds=60)
        cache.put("hello world", [0.1, 0.2])
        assert cache.get("hello world") == [0.1, 0.2]


class TestCachedEmbedder:
    @pytest.mark.asyncio
    async def test_caches_embed_calls(self):
        call_count = 0

        class MockEmbedder:
            dimensions = 3

            async def embed(self, text):
                nonlocal call_count
                call_count += 1
                return [0.1, 0.2, 0.3]

            async def embed_batch(self, texts):
                nonlocal call_count
                call_count += len(texts)
                return [[0.1, 0.2, 0.3]] * len(texts)

        cache = EmbeddingCache(max_size=100, ttl_seconds=60)
        cached = CachedEmbedder(MockEmbedder(), cache)

        # First call — cache miss
        result1 = await cached.embed("hello")
        assert result1 == [0.1, 0.2, 0.3]
        assert call_count == 1

        # Second call — cache hit, no new embed call
        result2 = await cached.embed("hello")
        assert result2 == [0.1, 0.2, 0.3]
        assert call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_batch_partial_cache(self):
        call_count = 0

        class MockEmbedder:
            dimensions = 3

            async def embed(self, text):
                nonlocal call_count
                call_count += 1
                return [0.1, 0.2, 0.3]

            async def embed_batch(self, texts):
                nonlocal call_count
                call_count += len(texts)
                return [[0.1, 0.2, 0.3]] * len(texts)

        cache = EmbeddingCache(max_size=100, ttl_seconds=60)
        cached = CachedEmbedder(MockEmbedder(), cache)

        # Pre-cache one text
        await cached.embed("hello")
        assert call_count == 1

        # Batch with one cached and one new
        results = await cached.embed_batch(["hello", "world"])
        assert len(results) == 2
        assert call_count == 2  # only "world" needed embedding


class TestSearchCache:
    def test_cache_and_retrieve(self):
        cache = SearchCache(max_size=10, ttl_seconds=60)
        cache.put("test query", {"cat": "electronics"}, 10, 0, "fake_response")
        assert cache.get("test query", {"cat": "electronics"}, 10, 0) == "fake_response"

    def test_different_params_different_cache(self):
        cache = SearchCache(max_size=10, ttl_seconds=60)
        cache.put("query", None, 10, 0, "response_10")
        cache.put("query", None, 5, 0, "response_5")
        assert cache.get("query", None, 10, 0) == "response_10"
        assert cache.get("query", None, 5, 0) == "response_5"

    def test_invalidate_all(self):
        cache = SearchCache(max_size=10, ttl_seconds=60)
        cache.put("q1", None, 10, 0, "r1")
        cache.put("q2", None, 10, 0, "r2")
        cache.invalidate_all()
        assert cache.get("q1", None, 10, 0) is None
        assert cache.get("q2", None, 10, 0) is None
