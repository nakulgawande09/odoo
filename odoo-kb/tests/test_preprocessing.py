"""Tests for query preprocessing."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_intent_extraction(preprocessor):
    result = await preprocessor.process("how to reset my password")
    assert result.intent == "how_to"

    result = await preprocessor.process("error connecting to database")
    assert result.intent == "troubleshooting"

    result = await preprocessor.process("what is a knowledge base")
    assert result.intent == "definition"


@pytest.mark.asyncio
async def test_filter_extraction(preprocessor):
    result = await preprocessor.process("return policy for electronics")
    assert result.filters.get("category") == "electronics"

    result = await preprocessor.process("tech products last 30 days")
    assert result.filters.get("category") == "electronics"  # alias
    assert result.filters.get("date_range") == "last_30_days"


@pytest.mark.asyncio
async def test_search_text_cleaned(preprocessor):
    result = await preprocessor.process("return policy for electronics")
    # "electronics" and "for" should be removed from search_text
    assert "electronics" not in result.search_text.lower()
    assert result.original_query == "return policy for electronics"
