"""FastAPI dependency injection — wires up services from settings."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from config.settings import Settings, load_filter_taxonomy


@lru_cache
def get_settings() -> Settings:
    return Settings()


# These are set during app lifespan startup
_backend: Any = None
_embedder: Any = None
_preprocessor: Any = None
_orchestrator: Any = None
_pipeline: Any = None
_query_logger: Any = None
_cache_stats_fn: Any = None
_voice_agent_store: Any = None
_answer_synthesizer: Any = None


def set_services(
    backend: Any,
    embedder: Any,
    preprocessor: Any,
    orchestrator: Any,
    pipeline: Any,
    query_logger: Any = None,
    cache_stats_fn: Any = None,
    voice_agent_store: Any = None,
    answer_synthesizer: Any = None,
) -> None:
    global _backend, _embedder, _preprocessor, _orchestrator, _pipeline
    global _query_logger, _cache_stats_fn, _voice_agent_store, _answer_synthesizer
    _backend = backend
    _embedder = embedder
    _preprocessor = preprocessor
    _orchestrator = orchestrator
    _pipeline = pipeline
    _query_logger = query_logger
    _cache_stats_fn = cache_stats_fn
    _voice_agent_store = voice_agent_store
    _answer_synthesizer = answer_synthesizer


def get_backend():
    return _backend


def get_embedder():
    return _embedder


def get_preprocessor():
    return _preprocessor


def get_orchestrator():
    return _orchestrator


def get_pipeline():
    return _pipeline


def get_query_logger():
    return _query_logger


def get_cache_stats():
    if _cache_stats_fn:
        return _cache_stats_fn()
    return None


def get_voice_agent_store():
    if _voice_agent_store is None:
        from app.core.voice_agent_config import VoiceAgentStore
        return VoiceAgentStore()  # Fallback empty store
    return _voice_agent_store


def get_answer_synthesizer():
    return _answer_synthesizer
