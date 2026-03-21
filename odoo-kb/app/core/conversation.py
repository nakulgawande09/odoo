"""Conversation context tracker for contextual search.

Stores recent queries per conversation_id so the search system can
use prior context to improve results — e.g., resolving pronouns
("it", "that product") or boosting topics from earlier turns.

Two implementations:
  - ConversationTracker: in-memory (dev / single instance)
  - RedisConversationTracker: Redis-backed (production / multi-instance)
"""
from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default TTL for conversation context (30 minutes)
DEFAULT_TTL_SECONDS = 1800
# Max conversations to track in memory
MAX_CONVERSATIONS = 10_000
# Max turns to keep per conversation
MAX_TURNS = 20


@dataclass
class ConversationTurn:
    """A single turn in a conversation."""
    query: str
    intent: str | None = None
    filters: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class ConversationContext:
    """Full context for a conversation."""
    conversation_id: str
    turns: list[ConversationTurn] = field(default_factory=list)
    last_active: float = field(default_factory=time.monotonic)

    def add_turn(self, query: str, intent: str | None = None, filters: dict | None = None) -> None:
        self.turns.append(ConversationTurn(
            query=query,
            intent=intent,
            filters=filters or {},
        ))
        if len(self.turns) > MAX_TURNS:
            self.turns = self.turns[-MAX_TURNS:]
        self.last_active = time.monotonic()

    @property
    def recent_queries(self) -> list[str]:
        """Get queries from the last few turns."""
        return [t.query for t in self.turns[-5:]]

    @property
    def accumulated_filters(self) -> dict:
        """Merge filters from recent turns (latest wins)."""
        merged: dict = {}
        for turn in self.turns[-5:]:
            merged.update(turn.filters)
        return merged

    @property
    def dominant_intent(self) -> str | None:
        """Most common intent in recent turns."""
        recent_intents = [t.intent for t in self.turns[-5:] if t.intent]
        if not recent_intents:
            return None
        from collections import Counter
        return Counter(recent_intents).most_common(1)[0][0]


class ConversationTracker:
    """In-memory conversation context store with TTL eviction.

    For production at scale, replace with Redis or a dedicated store.
    This in-memory implementation is suitable for single-instance deployments.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._conversations: OrderedDict[str, ConversationContext] = OrderedDict()
        self._ttl = ttl_seconds

    def get_context(self, conversation_id: str) -> ConversationContext | None:
        """Get context for a conversation, or None if expired/missing."""
        self._evict_expired()
        ctx = self._conversations.get(conversation_id)
        if ctx is None:
            return None
        return ctx

    def record_turn(
        self,
        conversation_id: str,
        query: str,
        intent: str | None = None,
        filters: dict | None = None,
    ) -> ConversationContext:
        """Record a new turn and return updated context."""
        self._evict_expired()

        if conversation_id not in self._conversations:
            if len(self._conversations) >= MAX_CONVERSATIONS:
                # Evict oldest
                self._conversations.popitem(last=False)
            self._conversations[conversation_id] = ConversationContext(
                conversation_id=conversation_id
            )

        ctx = self._conversations[conversation_id]
        ctx.add_turn(query, intent, filters)
        # Move to end (most recently used)
        self._conversations.move_to_end(conversation_id)
        return ctx

    def expand_query_with_context(
        self, query: str, conversation_id: str | None
    ) -> str:
        """Expand a query using conversation context.

        If the query contains pronouns or is very short, prepend
        context from recent turns to help the search system.
        """
        if not conversation_id:
            return query

        ctx = self.get_context(conversation_id)
        if ctx is None or not ctx.turns:
            return query

        # Detect if query needs context (short or contains pronouns)
        needs_context = (
            len(query.split()) <= 3
            or any(
                p in query.lower().split()
                for p in ("it", "that", "this", "those", "them", "its")
            )
        )

        if not needs_context:
            return query

        # Prepend the most recent prior query for context
        recent = ctx.recent_queries
        if len(recent) >= 2:
            # Use the previous query (not the current one being processed)
            prior = recent[-2] if recent[-1] == query else recent[-1]
            return f"{prior} {query}"

        return query

    def get_context_filters(self, conversation_id: str | None) -> dict:
        """Get accumulated filters from conversation history."""
        if not conversation_id:
            return {}
        ctx = self.get_context(conversation_id)
        if ctx is None:
            return {}
        return ctx.accumulated_filters

    def _evict_expired(self) -> None:
        """Remove conversations older than TTL."""
        now = time.monotonic()
        expired = [
            cid
            for cid, ctx in self._conversations.items()
            if now - ctx.last_active > self._ttl
        ]
        for cid in expired:
            del self._conversations[cid]


class RedisConversationTracker:
    """Redis-backed conversation context store.

    Each conversation is stored as a JSON blob at key ``kb:conv:{id}``
    with a Redis TTL for automatic expiry. Suitable for multi-instance
    deployments.
    """

    _KEY_PREFIX = "kb:conv:"

    def __init__(self, redis: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._r = redis
        self._ttl = ttl_seconds

    async def _load(self, conversation_id: str) -> ConversationContext | None:
        raw = await self._r.get(f"{self._KEY_PREFIX}{conversation_id}")
        if raw is None:
            return None
        data = json.loads(raw)
        turns = [ConversationTurn(**t) for t in data.get("turns", [])]
        return ConversationContext(
            conversation_id=data["conversation_id"],
            turns=turns,
            last_active=data.get("last_active", time.monotonic()),
        )

    async def _save(self, ctx: ConversationContext) -> None:
        data = {
            "conversation_id": ctx.conversation_id,
            "turns": [asdict(t) for t in ctx.turns],
            "last_active": ctx.last_active,
        }
        await self._r.set(
            f"{self._KEY_PREFIX}{ctx.conversation_id}",
            json.dumps(data),
            ex=self._ttl,
        )

    async def get_context(self, conversation_id: str) -> ConversationContext | None:
        return await self._load(conversation_id)

    async def record_turn(
        self,
        conversation_id: str,
        query: str,
        intent: str | None = None,
        filters: dict | None = None,
    ) -> ConversationContext:
        ctx = await self._load(conversation_id)
        if ctx is None:
            ctx = ConversationContext(conversation_id=conversation_id)
        ctx.add_turn(query, intent, filters)
        await self._save(ctx)
        return ctx

    def expand_query_with_context(
        self, query: str, conversation_id: str | None
    ) -> str:
        """Synchronous — cannot use Redis. Returns query unchanged.

        For Redis-backed tracker, query expansion should be done after
        an async ``get_context()`` call by the orchestrator. This method
        exists only for interface compatibility.
        """
        return query

    async def expand_query_with_context_async(
        self, query: str, conversation_id: str | None
    ) -> str:
        """Async query expansion using Redis-stored conversation context."""
        if not conversation_id:
            return query
        ctx = await self.get_context(conversation_id)
        if ctx is None or not ctx.turns:
            return query

        needs_context = (
            len(query.split()) <= 3
            or any(
                p in query.lower().split()
                for p in ("it", "that", "this", "those", "them", "its")
            )
        )
        if not needs_context:
            return query

        recent = ctx.recent_queries
        if len(recent) >= 2:
            prior = recent[-2] if recent[-1] == query else recent[-1]
            return f"{prior} {query}"
        return query

    def get_context_filters(self, conversation_id: str | None) -> dict:
        """Synchronous stub — returns empty. Use async version."""
        return {}

    async def get_context_filters_async(self, conversation_id: str | None) -> dict:
        if not conversation_id:
            return {}
        ctx = await self.get_context(conversation_id)
        if ctx is None:
            return {}
        return ctx.accumulated_filters
