"""Tests for VOIP integration layer and voice agent config."""
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
from app.core.voice_agent_config import VoiceAgentConfig, VoiceAgentStore
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
    assert req.agent_id is None


def test_voip_query_request_with_provider():
    req = VOIPQueryRequest(
        query="shipping info",
        call_id="call-123",
        provider="twilio",
        caller_number="+1234567890",
        agent_id=5,
    )
    assert req.provider == "twilio"
    assert req.agent_id == 5


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
    assert resp.agent_name is None


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
    assert "not" in answer.lower() or "transfer" in answer.lower()


def test_build_answer_with_custom_config():
    config = VoiceAgentConfig(
        agent_id=1,
        no_answer_message="Sorry, no info found.",
        low_confidence_message="Let me get a human.",
        confidence_threshold=0.5,
        max_answer_length=100,
    )

    # No results
    answer, conf = _build_answer([], config)
    assert answer == "Sorry, no info found."

    # Low confidence (below custom 0.5 threshold)
    results = [_make_result("1", 0.4, "Some content")]
    answer, conf = _build_answer(results, config)
    assert answer == "Let me get a human."

    # Good confidence
    results = [_make_result("1", 0.8, "A" * 200)]
    answer, conf = _build_answer(results, config)
    assert len(answer) <= 110  # ~100 + sentence boundary tolerance


def test_build_answer_truncates_long_content():
    long_content = "This is a sentence. " * 100
    results = [_make_result("1", 0.9, long_content)]
    answer, _ = _build_answer(results)
    assert len(answer) <= 510


# ─── Follow-up Suggestions ────────────────────────────────────

def test_suggest_follow_up_known_intent():
    assert _suggest_follow_up("return_policy") is not None
    assert "return" in _suggest_follow_up("return_policy").lower()


def test_suggest_follow_up_pricing():
    assert _suggest_follow_up("pricing") is not None


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


def test_twilio_response_uses_agent_config():
    config = VoiceAgentConfig(agent_id=1, tts_language="fr-FR", tts_voice="female")
    resp = _twilio_response("Bonjour", gather=True, config=config)
    assert resp["language"] == "fr-FR"
    assert resp["voice"] == "female"


# ─── Voice Agent Store ────────────────────────────────────────

def test_store_put_and_get():
    store = VoiceAgentStore()
    store.put(1, {"name": "Sales Agent", "provider": "twilio", "confidence_threshold": 0.5})

    config = store.get(1)
    assert config.name == "Sales Agent"
    assert config.provider == "twilio"
    assert config.confidence_threshold == 0.5


def test_store_get_default():
    store = VoiceAgentStore()
    config = store.get(None)
    assert config.agent_id == 0
    assert config.name == "Default Voice Agent"


def test_store_get_missing_returns_default():
    store = VoiceAgentStore()
    config = store.get(999)
    assert config.agent_id == 0


def test_store_delete():
    store = VoiceAgentStore()
    store.put(1, {"name": "Agent 1"})
    assert store.delete(1) is True
    assert store.get(1).agent_id == 0  # back to default
    assert store.delete(999) is False


def test_store_list_all():
    store = VoiceAgentStore()
    store.put(1, {"name": "Agent 1"})
    store.put(2, {"name": "Agent 2"})
    agents = store.list_all()
    assert len(agents) == 2


def test_store_update_existing():
    store = VoiceAgentStore()
    store.put(1, {"name": "Old Name"})
    store.put(1, {"name": "New Name"})
    assert store.get(1).name == "New Name"
    assert len(store.list_all()) == 1
