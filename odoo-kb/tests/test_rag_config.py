"""Tests for RAG configuration."""
from __future__ import annotations

from app.core.rag_config import RAGConfig, VOIP_SYSTEM_PROMPT, SEARCH_SYSTEM_PROMPT
from app.core.voice_agent_config import VoiceAgentConfig


def test_rag_config_defaults():
    config = RAGConfig()
    assert config.enabled is False
    assert config.llm_provider == "openai"
    assert config.max_tokens == 300


def test_rag_config_channel_prompts():
    voip = RAGConfig(channel="voip")
    assert voip.get_system_prompt() == VOIP_SYSTEM_PROMPT

    search = RAGConfig(channel="search")
    assert search.get_system_prompt() == SEARCH_SYSTEM_PROMPT


def test_rag_config_custom_prompt_overrides_channel():
    config = RAGConfig(channel="voip", system_prompt="Custom prompt here.")
    assert config.get_system_prompt() == "Custom prompt here."


def test_voice_agent_to_rag_config():
    agent = VoiceAgentConfig(
        agent_id=1,
        rag_enabled=True,
        rag_llm_provider="anthropic",
        rag_llm_model="claude-sonnet-4-20250514",
        rag_system_prompt="Be concise.",
        rag_max_tokens=200,
        rag_temperature=0.5,
        rag_max_context_chunks=3,
    )

    rag = agent.to_rag_config()

    assert rag.enabled is True
    assert rag.llm_provider == "anthropic"
    assert rag.llm_model == "claude-sonnet-4-20250514"
    assert rag.system_prompt == "Be concise."
    assert rag.max_tokens == 200
    assert rag.temperature == 0.5
    assert rag.max_context_chunks == 3
    assert rag.channel == "voip"


def test_voice_agent_default_rag_disabled():
    agent = VoiceAgentConfig(agent_id=1)
    rag = agent.to_rag_config()
    assert rag.enabled is False
