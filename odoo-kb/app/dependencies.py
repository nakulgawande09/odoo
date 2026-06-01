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
_tts_provider: Any = None
_call_tracker: Any = None
_session_factory: Any = None
_crm_analyzer: Any = None
_vonage_messages_client: Any = None
_answer_generator: Any = None
_stt_provider: Any = None


def set_services(
    backend: Any,
    embedder: Any,
    preprocessor: Any,
    orchestrator: Any,
    pipeline: Any,
    query_logger: Any = None,
    cache_stats_fn: Any = None,
    voice_agent_store: Any = None,
    tts_provider: Any = None,
    call_tracker: Any = None,
    session_factory: Any = None,
    crm_analyzer: Any = None,
    vonage_messages_client: Any = None,
    answer_generator: Any = None,
    stt_provider: Any = None,
) -> None:
    global _backend, _embedder, _preprocessor, _orchestrator, _pipeline
    global _query_logger, _cache_stats_fn, _voice_agent_store, _tts_provider
    global _call_tracker, _session_factory, _crm_analyzer
    global _vonage_messages_client, _answer_generator, _stt_provider
    _backend = backend
    _embedder = embedder
    _preprocessor = preprocessor
    _orchestrator = orchestrator
    _pipeline = pipeline
    _query_logger = query_logger
    _cache_stats_fn = cache_stats_fn
    _voice_agent_store = voice_agent_store
    _tts_provider = tts_provider
    _call_tracker = call_tracker
    _session_factory = session_factory
    _crm_analyzer = crm_analyzer
    _vonage_messages_client = vonage_messages_client
    _answer_generator = answer_generator
    _stt_provider = stt_provider


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


def get_tts_provider():
    return _tts_provider


def get_voice_agent_store():
    if _voice_agent_store is None:
        from app.core.voice_agent_config import VoiceAgentStore
        return VoiceAgentStore()  # Fallback empty store
    return _voice_agent_store


def get_call_tracker():
    return _call_tracker


def get_session_factory():
    return _session_factory


def get_crm_analyzer():
    return _crm_analyzer


def get_vonage_messages_client():
    return _vonage_messages_client


def get_answer_generator():
    return _answer_generator


def get_stt_provider():
    return _stt_provider
