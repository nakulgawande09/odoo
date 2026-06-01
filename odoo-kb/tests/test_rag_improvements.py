"""Regression tests for the RAG-quality fixes:

- Reranker dispatcher recognises the new "gemini" provider.
- StaticQueryExpander merges domain synonyms with defaults rather than
  replacing them.
- load_synonyms() parses config/synonyms.yaml correctly.

End-to-end tests against the live KB + Gemini are out of scope here --
they require a populated database and external API credentials.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.query_expansion import DEFAULT_SYNONYMS, StaticQueryExpander
from app.core.reranker import CrossEncoderReranker, create_reranker
from app.schemas.search import SearchResultItem
from config.settings import load_synonyms


class _Settings:
    reranker_provider = "gemini"
    reranker_model = "gemini-2.5-flash"
    reranker_top_k = 20
    openai_api_key = ""
    gemini_api_key = "fake-key-for-testing"


def _result(doc_id: str, score: float, content: str) -> SearchResultItem:
    return SearchResultItem(
        document_id=doc_id,
        chunk_id=f"chunk-{doc_id}",
        title=f"Doc {doc_id}",
        snippet=content[:200],
        content=content,
        relevance_score=score,
        source_backend="pgvector",
    )


def test_create_reranker_accepts_gemini_provider():
    """The factory must construct a reranker for provider='gemini'."""
    reranker = create_reranker(_Settings())
    assert isinstance(reranker, CrossEncoderReranker)
    assert reranker._provider == "gemini"


@pytest.mark.asyncio
async def test_gemini_reranker_falls_back_when_api_unreachable(monkeypatch):
    """If Gemini errors, the reranker preserves original ordering rather than crash."""
    settings = _Settings()
    reranker = CrossEncoderReranker(settings)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("gemini api unreachable")

    monkeypatch.setattr(reranker, "_rerank_gemini", boom)

    results = [_result("1", 0.9, "alpha"), _result("2", 0.5, "beta")]
    out = await reranker.rerank("query", results)
    assert [r.document_id for r in out] == ["1", "2"]
    assert out[0].relevance_score == 0.9


@pytest.mark.asyncio
async def test_domain_synonyms_merge_with_defaults():
    """Custom synonyms must not erase DEFAULT_SYNONYMS keys."""
    expander = StaticQueryExpander(synonyms={"klay": ["Klay", "KLAY"]})

    # Default behaviour preserved
    out_default = await expander.expand("how do I cancel my order")
    assert any(syn in out_default for syn in DEFAULT_SYNONYMS["cancel"])

    # Domain key is reachable from reverse lookup
    assert "klay" in expander._reverse.values()


@pytest.mark.asyncio
async def test_domain_synonym_reverse_lookup():
    """Querying with a synonym variant should expand to the canonical term."""
    expander = StaticQueryExpander(
        synonyms={"afterschool": ["after-school", "after school", "Afterschool Enrichment"]}
    )
    out = await expander.expand("activities in the after school program")
    # 'after school' is a synonym → canonical 'afterschool' should be added
    assert "afterschool" in out.lower()


def test_load_synonyms_reads_klay_yaml():
    """The shipped config/synonyms.yaml must load and contain KLAY-domain keys."""
    repo_root = Path(__file__).resolve().parent.parent
    syn = load_synonyms(str(repo_root / "config" / "synonyms.yaml"))
    assert syn  # non-empty
    # Spot-check core entries from the failing-query post-mortem
    for key in ("klay", "nanny", "afterschool", "pillar", "lap"):
        assert key in syn, f"expected {key!r} key in synonyms.yaml"


def test_load_synonyms_missing_file_returns_empty():
    syn = load_synonyms("/nonexistent/path/synonyms.yaml")
    assert syn == {}
