"""Tests for conversation context tracking."""
from __future__ import annotations

import pytest

from app.core.conversation import ConversationTracker, ConversationContext


@pytest.fixture
def tracker():
    return ConversationTracker(ttl_seconds=60)


def test_record_and_retrieve(tracker):
    ctx = tracker.record_turn("conv-1", "what is a refund policy")
    assert ctx.conversation_id == "conv-1"
    assert len(ctx.turns) == 1
    assert ctx.turns[0].query == "what is a refund policy"

    # Second turn
    tracker.record_turn("conv-1", "how do I request one")
    ctx = tracker.get_context("conv-1")
    assert len(ctx.turns) == 2


def test_missing_conversation(tracker):
    assert tracker.get_context("nonexistent") is None


def test_expand_query_with_pronouns(tracker):
    tracker.record_turn("conv-1", "tell me about the return policy")
    tracker.record_turn("conv-1", "what are the exceptions")

    # "it" triggers context expansion
    expanded = tracker.expand_query_with_context("how does it work", "conv-1")
    assert "return policy" in expanded or "exceptions" in expanded


def test_expand_short_query(tracker):
    tracker.record_turn("conv-1", "shipping options for electronics")
    # Need at least 2 turns for context — the current query is the 2nd turn
    tracker.record_turn("conv-1", "cost")
    expanded = tracker.expand_query_with_context("cost", "conv-1")
    # Short query (1 word) should get expanded with prior context
    assert "shipping" in expanded or "electronics" in expanded


def test_no_expansion_without_context(tracker):
    result = tracker.expand_query_with_context("normal query here", None)
    assert result == "normal query here"


def test_no_expansion_for_clear_query(tracker):
    tracker.record_turn("conv-1", "tell me about returns")
    # Clear query with enough words and no pronouns — no expansion
    result = tracker.expand_query_with_context(
        "what are the shipping options available", "conv-1"
    )
    assert result == "what are the shipping options available"


def test_accumulated_filters(tracker):
    tracker.record_turn("conv-1", "q1", filters={"category": "electronics"})
    tracker.record_turn("conv-1", "q2", filters={"date_range": "last_30_days"})

    ctx = tracker.get_context("conv-1")
    accumulated = ctx.accumulated_filters
    assert accumulated["category"] == "electronics"
    assert accumulated["date_range"] == "last_30_days"


def test_dominant_intent(tracker):
    tracker.record_turn("conv-1", "q1", intent="how_to")
    tracker.record_turn("conv-1", "q2", intent="how_to")
    tracker.record_turn("conv-1", "q3", intent="troubleshooting")

    ctx = tracker.get_context("conv-1")
    assert ctx.dominant_intent == "how_to"


def test_max_conversations_eviction(tracker):
    # Fill beyond limit
    from app.core.conversation import MAX_CONVERSATIONS
    for i in range(MAX_CONVERSATIONS + 5):
        tracker.record_turn(f"conv-{i}", f"query {i}")

    # Oldest should be evicted
    assert tracker.get_context("conv-0") is None
    assert tracker.get_context(f"conv-{MAX_CONVERSATIONS + 4}") is not None
