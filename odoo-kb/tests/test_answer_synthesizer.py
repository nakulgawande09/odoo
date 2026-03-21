"""Tests for RAG answer synthesis."""
from __future__ import annotations

import pytest

from app.core.answer_synthesizer import AnswerSynthesizer, SynthesizedAnswer
from app.core.rag_config import RAGConfig, VOIP_SYSTEM_PROMPT
from app.schemas.search import SearchResultItem


def _make_result(doc_id: str, score: float, content: str = "Test content.") -> SearchResultItem:
    return SearchResultItem(
        document_id=doc_id,
        chunk_id=f"chunk-{doc_id}",
        title=f"Doc {doc_id}",
        snippet=content[:100],
        content=content,
        relevance_score=score,
        source_backend="pgvector",
    )


class MockLLMProvider:
    """Returns a canned response for testing."""

    def __init__(self, response: str = "This is a synthesized answer."):
        self._response = response
        self.last_messages: list[dict[str, str]] | None = None

    async def generate(self, messages, **kwargs):
        self.last_messages = messages
        return self._response

    async def generate_stream(self, messages, **kwargs):
        self.last_messages = messages
        for word in self._response.split():
            yield word + " "


class FailingLLMProvider:
    """Always raises an exception."""

    async def generate(self, messages, **kwargs):
        raise ConnectionError("LLM unavailable")

    async def generate_stream(self, messages, **kwargs):
        raise ConnectionError("LLM unavailable")
        yield  # make it an async generator


@pytest.mark.asyncio
async def test_synthesize_basic():
    mock = MockLLMProvider("The return policy allows 30-day returns.")
    synth = AnswerSynthesizer(mock)
    config = RAGConfig(enabled=True, channel="voip")

    results = [
        _make_result("1", 0.9, "Our return policy is 30 days."),
        _make_result("2", 0.7, "Shipping takes 3-5 days."),
        _make_result("3", 0.5, "Contact support for help."),
    ]

    answer = await synth.synthesize("What is the return policy?", results, config)

    assert answer.answer == "The return policy allows 30-day returns."
    assert answer.fallback_used is False
    assert answer.model_used == "gpt-4o-mini"
    assert answer.synthesis_time_ms >= 0


@pytest.mark.asyncio
async def test_synthesize_fallback_on_llm_failure():
    synth = AnswerSynthesizer(FailingLLMProvider())
    config = RAGConfig(enabled=True, channel="voip")

    results = [_make_result("1", 0.9, "Fallback content here.")]

    answer = await synth.synthesize("test query", results, config)

    assert answer.answer == "Fallback content here."
    assert answer.fallback_used is True


@pytest.mark.asyncio
async def test_synthesize_no_results():
    mock = MockLLMProvider()
    synth = AnswerSynthesizer(mock)
    config = RAGConfig(enabled=True, channel="voip")

    answer = await synth.synthesize("test query", [], config)

    assert "don't have that information" in answer.answer
    assert answer.fallback_used is True
    assert mock.last_messages is None  # LLM should not be called


@pytest.mark.asyncio
async def test_synthesize_respects_max_context_chunks():
    mock = MockLLMProvider("Short answer.")
    synth = AnswerSynthesizer(mock)
    config = RAGConfig(enabled=True, max_context_chunks=3, channel="search")

    results = [_make_result(str(i), 0.9 - i * 0.1, f"Content {i}") for i in range(10)]

    await synth.synthesize("query", results, config)

    # Check that only 3 passages appear in the prompt
    user_msg = mock.last_messages[1]["content"]
    assert "[Passage 3]" in user_msg
    assert "[Passage 4]" not in user_msg


@pytest.mark.asyncio
async def test_synthesize_custom_system_prompt():
    mock = MockLLMProvider("Answer.")
    synth = AnswerSynthesizer(mock)
    config = RAGConfig(
        enabled=True,
        system_prompt="You are a custom assistant. Be very brief.",
        channel="voip",
    )

    results = [_make_result("1", 0.9)]
    await synth.synthesize("query", results, config)

    system_msg = mock.last_messages[0]["content"]
    assert "custom assistant" in system_msg


@pytest.mark.asyncio
async def test_synthesize_voip_prompt_no_markdown():
    mock = MockLLMProvider("Answer.")
    synth = AnswerSynthesizer(mock)
    config = RAGConfig(enabled=True, channel="voip")

    results = [_make_result("1", 0.9)]
    await synth.synthesize("query", results, config)

    system_msg = mock.last_messages[0]["content"]
    assert "no markdown" in system_msg.lower() or "No markdown" in system_msg


@pytest.mark.asyncio
async def test_synthesize_stream():
    mock = MockLLMProvider("The answer is forty two.")
    synth = AnswerSynthesizer(mock)
    config = RAGConfig(enabled=True, channel="search")

    results = [_make_result("1", 0.9)]
    tokens = []
    async for token in synth.synthesize_stream("query", results, config):
        tokens.append(token)

    combined = "".join(tokens).strip()
    assert "forty two" in combined


@pytest.mark.asyncio
async def test_synthesize_stream_fallback():
    synth = AnswerSynthesizer(FailingLLMProvider())
    config = RAGConfig(enabled=True, channel="search")

    results = [_make_result("1", 0.9, "Raw fallback content.")]
    tokens = []
    async for token in synth.synthesize_stream("query", results, config):
        tokens.append(token)

    assert "Raw fallback content." in "".join(tokens)
