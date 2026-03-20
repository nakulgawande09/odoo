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


def set_services(
    backend: Any,
    embedder: Any,
    preprocessor: Any,
    orchestrator: Any,
    pipeline: Any,
) -> None:
    global _backend, _embedder, _preprocessor, _orchestrator, _pipeline
    _backend = backend
    _embedder = embedder
    _preprocessor = preprocessor
    _orchestrator = orchestrator
    _pipeline = pipeline


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
