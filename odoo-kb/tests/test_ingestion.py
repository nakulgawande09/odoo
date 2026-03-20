"""Tests for ingestion components."""
from __future__ import annotations

import pytest

from app.ingestion.chunker import RecursiveChunker
from app.ingestion.extractors.text import TextExtractor
from app.ingestion.extractors.html import HtmlExtractor
from app.ingestion.extractors.csv_extractor import CsvExtractor


def test_chunker_small_text(chunker):
    result = chunker.chunk("Short text.")
    assert len(result) == 1
    assert result[0] == "Short text."


def test_chunker_splits_long_text(chunker):
    # chunker has max_chunk_size=100
    text = "A" * 50 + "\n\n" + "B" * 50 + "\n\n" + "C" * 50
    result = chunker.chunk(text)
    assert len(result) > 1


def test_chunker_empty():
    c = RecursiveChunker()
    assert c.chunk("") == []
    assert c.chunk("   ") == []


@pytest.mark.asyncio
async def test_text_extractor():
    ext = TextExtractor()
    assert "text/plain" in ext.supported_types
    result = await ext.extract("hello world", "text/plain")
    assert result == "hello world"

    result = await ext.extract(b"bytes content", "text/plain")
    assert result == "bytes content"


@pytest.mark.asyncio
async def test_html_extractor():
    ext = HtmlExtractor()
    html = "<html><body><p>Hello</p><script>evil()</script></body></html>"
    result = await ext.extract(html, "text/html")
    assert "Hello" in result
    assert "evil" not in result


@pytest.mark.asyncio
async def test_csv_extractor():
    ext = CsvExtractor()
    csv_content = "name,age\nAlice,30\nBob,25"
    result = await ext.extract(csv_content, "text/csv")
    assert "Alice" in result
    assert "name: Alice" in result
    assert "age: 30" in result
