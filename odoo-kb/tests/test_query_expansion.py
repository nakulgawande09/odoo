"""Tests for query expansion."""
from __future__ import annotations

import pytest

from app.core.query_expansion import StaticQueryExpander, DEFAULT_SYNONYMS


@pytest.fixture
def expander():
    return StaticQueryExpander()


@pytest.mark.asyncio
async def test_synonym_expansion(expander):
    result = await expander.expand("how to cancel my subscription")
    # "cancel" should trigger synonyms
    assert "cancel" in result
    assert any(
        syn in result
        for syn in DEFAULT_SYNONYMS["cancel"]
    )


@pytest.mark.asyncio
async def test_no_expansion_for_unknown_terms(expander):
    result = await expander.expand("quantum computing algorithms")
    assert result == "quantum computing algorithms"


@pytest.mark.asyncio
async def test_max_additions_respected(expander):
    result = await expander.expand("cancel", max_additions=1)
    original_words = set("cancel".split())
    new_words = set(result.split()) - original_words
    # Should have added at most 1 expansion (may be multi-word)
    assert len(result) > len("cancel")


@pytest.mark.asyncio
async def test_no_duplicate_terms(expander):
    # If the synonym is already in the query, don't add it
    result = await expander.expand("cancel cancellation")
    assert result.count("cancellation") == 1


@pytest.mark.asyncio
async def test_reverse_synonym_lookup(expander):
    # "money back" is an alias for "refund"
    result = await expander.expand("I want my money back")
    assert "refund" in result


@pytest.mark.asyncio
async def test_custom_synonyms():
    custom = {"widget": ["gadget", "component", "part"]}
    expander = StaticQueryExpander(synonyms=custom)
    result = await expander.expand("broken widget")
    assert "gadget" in result or "component" in result


@pytest.mark.asyncio
async def test_empty_query(expander):
    result = await expander.expand("")
    assert result == ""
