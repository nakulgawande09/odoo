"""Tests for LLM provider factory and initialization."""
from __future__ import annotations

from app.core.llm_provider import (
    AnthropicLLMProvider,
    OllamaLLMProvider,
    OpenAILLMProvider,
    create_llm_provider,
)


class MockSettings:
    rag_enabled = True
    rag_llm_provider = "openai"
    rag_llm_model = "gpt-4o-mini"
    openai_api_key = "sk-test"
    anthropic_api_key = ""
    ollama_url = "http://localhost:11434"


def test_create_provider_openai():
    settings = MockSettings()
    provider = create_llm_provider(settings)
    assert isinstance(provider, OpenAILLMProvider)


def test_create_provider_anthropic():
    settings = MockSettings()
    settings.rag_llm_provider = "anthropic"
    settings.anthropic_api_key = "sk-ant-test"
    provider = create_llm_provider(settings)
    assert isinstance(provider, AnthropicLLMProvider)


def test_create_provider_anthropic_no_key():
    settings = MockSettings()
    settings.rag_llm_provider = "anthropic"
    settings.anthropic_api_key = ""
    provider = create_llm_provider(settings)
    assert provider is None


def test_create_provider_ollama():
    settings = MockSettings()
    settings.rag_llm_provider = "ollama"
    provider = create_llm_provider(settings)
    assert isinstance(provider, OllamaLLMProvider)


def test_create_provider_none():
    settings = MockSettings()
    settings.rag_llm_provider = "none"
    provider = create_llm_provider(settings)
    assert provider is None


def test_create_provider_disabled():
    settings = MockSettings()
    settings.rag_enabled = False
    provider = create_llm_provider(settings)
    assert provider is None


def test_create_provider_unknown():
    settings = MockSettings()
    settings.rag_llm_provider = "unknown_provider"
    provider = create_llm_provider(settings)
    assert provider is None
