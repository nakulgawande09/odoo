"""Tests for Redis-backed stores using fakeredis."""
from __future__ import annotations

import pytest

try:
    import fakeredis.aioredis
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

from app.core.voice_agent_config import RedisVoiceAgentStore, VoiceAgentConfig
from app.core.conversation import RedisConversationTracker
from app.core.redis_cache import RedisEmbeddingCache, RedisSearchCache

pytestmark = pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")


@pytest.fixture
def redis():
    """Create a fakeredis instance for testing."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ─── RedisVoiceAgentStore ────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_agent_store_put_and_get(redis):
    store = RedisVoiceAgentStore(redis)
    agent = await store.put(1, {"name": "Test Agent", "provider": "twilio"})
    assert agent.name == "Test Agent"
    assert agent.provider == "twilio"

    loaded = await store.get(1)
    assert loaded.name == "Test Agent"
    assert loaded.agent_id == 1


@pytest.mark.asyncio
async def test_redis_agent_store_get_missing_returns_default(redis):
    store = RedisVoiceAgentStore(redis)
    config = await store.get(999)
    assert config.agent_id == 0  # DEFAULT_CONFIG
    assert config.name == "Default Voice Agent"


@pytest.mark.asyncio
async def test_redis_agent_store_get_none_returns_default(redis):
    store = RedisVoiceAgentStore(redis)
    config = await store.get(None)
    assert config.agent_id == 0


@pytest.mark.asyncio
async def test_redis_agent_store_delete(redis):
    store = RedisVoiceAgentStore(redis)
    await store.put(1, {"name": "Agent"})
    deleted = await store.delete(1)
    assert deleted is True

    config = await store.get(1)
    assert config.agent_id == 0  # Falls back to default


@pytest.mark.asyncio
async def test_redis_agent_store_delete_missing(redis):
    store = RedisVoiceAgentStore(redis)
    deleted = await store.delete(999)
    assert deleted is False


@pytest.mark.asyncio
async def test_redis_agent_store_list_all(redis):
    store = RedisVoiceAgentStore(redis)
    await store.put(1, {"name": "Agent A"})
    await store.put(2, {"name": "Agent B"})

    agents = await store.list_all()
    names = sorted(a.name for a in agents)
    assert names == ["Agent A", "Agent B"]


@pytest.mark.asyncio
async def test_redis_agent_store_rag_fields(redis):
    store = RedisVoiceAgentStore(redis)
    await store.put(1, {
        "name": "RAG Agent",
        "rag_enabled": True,
        "rag_llm_provider": "anthropic",
        "rag_llm_model": "claude-sonnet-4-20250514",
    })
    loaded = await store.get(1)
    assert loaded.rag_enabled is True
    assert loaded.rag_llm_provider == "anthropic"


# ─── RedisConversationTracker ────────────────────────────────

@pytest.mark.asyncio
async def test_redis_conversation_record_and_get(redis):
    tracker = RedisConversationTracker(redis, ttl_seconds=300)

    ctx = await tracker.record_turn("call-1", "What is the return policy?", intent="return_policy")
    assert ctx.conversation_id == "call-1"
    assert len(ctx.turns) == 1
    assert ctx.turns[0].query == "What is the return policy?"

    # Record another turn
    ctx = await tracker.record_turn("call-1", "How long do I have?")
    assert len(ctx.turns) == 2

    # Get context
    loaded = await tracker.get_context("call-1")
    assert loaded is not None
    assert len(loaded.turns) == 2


@pytest.mark.asyncio
async def test_redis_conversation_get_missing(redis):
    tracker = RedisConversationTracker(redis)
    ctx = await tracker.get_context("nonexistent")
    assert ctx is None


@pytest.mark.asyncio
async def test_redis_conversation_expand_query_async(redis):
    tracker = RedisConversationTracker(redis)
    await tracker.record_turn("call-1", "Tell me about the return policy")
    await tracker.record_turn("call-1", "How long is it?")

    # "it" is a pronoun trigger; the most recent stored query is "How long is it?"
    # The second-most-recent is "Tell me about the return policy"
    # Since "it" != recent[-1], prior = recent[-1] = "How long is it?"
    expanded = await tracker.expand_query_with_context_async("it", "call-1")
    assert "it" in expanded
    assert len(expanded) > 2  # should be expanded, not just "it"


@pytest.mark.asyncio
async def test_redis_conversation_filters_async(redis):
    tracker = RedisConversationTracker(redis)
    await tracker.record_turn("call-1", "Show me laptops", filters={"category": "laptops"})

    filters = await tracker.get_context_filters_async("call-1")
    assert filters == {"category": "laptops"}


# ─── RedisEmbeddingCache ─────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_embedding_cache_put_and_get(redis):
    cache = RedisEmbeddingCache(redis, ttl_seconds=60)

    embedding = [0.1, 0.2, 0.3, 0.4]
    await cache.put("hello world", embedding)

    result = await cache.get("hello world")
    assert result == embedding


@pytest.mark.asyncio
async def test_redis_embedding_cache_miss(redis):
    cache = RedisEmbeddingCache(redis, ttl_seconds=60)
    result = await cache.get("unknown text")
    assert result is None


@pytest.mark.asyncio
async def test_redis_embedding_cache_stats(redis):
    cache = RedisEmbeddingCache(redis, ttl_seconds=60)
    await cache.put("test", [1.0])
    await cache.get("test")  # hit
    await cache.get("miss")  # miss

    stats = cache.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["backend"] == "redis"


# ─── RedisSearchCache ────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_search_cache_put_and_get(redis):
    cache = RedisSearchCache(redis, ttl_seconds=60)

    response_data = {"query": "test", "results": []}
    await cache.put("test query", None, 10, 0, response_data)

    result = await cache.get("test query", None, 10, 0)
    assert result == response_data


@pytest.mark.asyncio
async def test_redis_search_cache_miss(redis):
    cache = RedisSearchCache(redis, ttl_seconds=60)
    result = await cache.get("unknown", None, 10, 0)
    assert result is None


@pytest.mark.asyncio
async def test_redis_search_cache_invalidate_all(redis):
    cache = RedisSearchCache(redis, ttl_seconds=60)
    await cache.put("q1", None, 10, 0, {"r": 1})
    await cache.put("q2", None, 10, 0, {"r": 2})

    await cache.invalidate_all()

    assert await cache.get("q1", None, 10, 0) is None
    assert await cache.get("q2", None, 10, 0) is None
