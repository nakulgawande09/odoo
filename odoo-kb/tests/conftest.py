"""Test fixtures."""
from __future__ import annotations

import pytest

from app.ingestion.chunker import RecursiveChunker
from app.core.query_preprocessor import RuleBasedPreprocessor


@pytest.fixture
def chunker():
    return RecursiveChunker(max_chunk_size=100, overlap=20)


@pytest.fixture
def preprocessor():
    taxonomy = {
        "filter_categories": {
            "category": {
                "type": "enum",
                "values": ["electronics", "clothing"],
                "aliases": {"tech": "electronics"},
            },
            "date_range": {
                "type": "temporal",
                "options": ["last_7_days", "last_30_days", "last_year"],
            },
        }
    }
    return RuleBasedPreprocessor(filter_taxonomy=taxonomy)
