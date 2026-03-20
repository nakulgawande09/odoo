"""Tests for VOIP integration layer."""
from __future__ import annotations

import pytest

from app.api.v1.voip import (
    VOIPQueryRequest,
    VOIPQueryResponse,
    VOIPSource,
    _build_answer,
    _suggest_follow_up,
    _twilio_response,
)
from app.schemas.search import SearchResultItem


def _make_result(doc_id: str, score: float, content: str = "Test content") -> SearchResultItem:
    return SearchResultItem(
        document_id=doc_id,
        chunk_id=f"chunk-{doc_id}",
        title=f"Doc {doc_id}",
        snippet=content[:100],
        content=content,
        relevance_score=score,
        source_backend="pgvector",
    )


# ─── Schema Tests ─────────────────────────────────────────────

def test_voip_query_request_defaults():
    req = VOIPQueryRequest(query="what is the return policy")
    assert req.provider == "generic"
    assert req.language == "en"
    assert req.max_results == 3
    assert req.call_id is None


def test_voip_query_request_with_provider():
    req = VOIPQueryRequest(
        query="shipping info",
        call_id="call-123",
        provider="twilio",
        caller_number="+1234567890",
    )
    assert req.provider == "twilio"
    assert req.call_id == "call-123"


def test_voip_source_model():
    source = VOIPSource(
        title="Return Policy",
        snippet="Returns within 30 days",
        relevance=0.95,
        document_id="doc-1",
    )
    assert source.relevance == 0.95


def test_voip_response_model():
    resp = VOIPQueryResponse(
        call_id="call-1",
        query="return policy",
        answer="Our return policy allows returns within 30 days.",
        confidence=0.9,
        sources=[],
        response_time_ms=45.2,
    )
    assert resp.confidence == 0.9
    assert resp.follow_up is None


# ─── Answer Builder Tests ─────────────────────────────────────

def test_build_answer_with_results():
    results = [
        _make_result("1", 0.9, "Returns are accepted within 30 days of purchase."),
        _make_result("2", 0.7, "Contact support for help."),
    ]
    answer, confidence = _build_answer(results)
    assert confidence == 0.9
    assert "30 days" in answer


def test_build_answer_no_results():
    answer, confidence = _build_answer([])
    assert confidence == 0.0
    assert "couldn't find" in answer.lower()


def test_build_answer_low_confidence():
    results = [_make_result("1", 0.2, "Some vague content")]
    answer, confidence = _build_answer(results)
    assert confidence == 0.2
    assert "not confident" in answer.lower() or "transfer" in answer.lower()


def test_build_answer_truncates_long_content():
    long_content = "This is a sentence. " * 100  # ~2000 chars
    results = [_make_result("1", 0.9, long_content)]
    answer, _ = _build_answer(results)
    assert len(answer) <= 510  # ~500 + sentence boundary tolerance


# ─── Follow-up Suggestions ────────────────────────────────────

def test_suggest_follow_up_known_intent():
    assert _suggest_follow_up("return_policy") is not None
    assert "return" in _suggest_follow_up("return_policy").lower()


def test_suggest_follow_up_pricing():
    assert _suggest_follow_up("pricing") is not None
    assert "plan" in _suggest_follow_up("pricing").lower()


def test_suggest_follow_up_unknown_intent():
    assert _suggest_follow_up("informational") is None
    assert _suggest_follow_up(None) is None


# ─── Twilio Response Builder ─────────────────────────────────

def test_twilio_response_with_gather():
    resp = _twilio_response("Hello, how can I help?", gather=True)
    assert resp["say"] == "Hello, how can I help?"
    assert resp["gather"] is True
    assert "gather_config" in resp


def test_twilio_response_without_gather():
    resp = _twilio_response("Transferring you now.", gather=False)
    assert resp["gather"] is False
    assert "gather_config" not in resp
