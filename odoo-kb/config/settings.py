from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Service
    service_name: str = "kb-service"
    host: str = "0.0.0.0"
    port: int = 8100
    workers: int = 4
    log_level: str = "info"

    # Auth
    api_keys: list[str] = []

    # Database (asyncpg for async pgvector)
    database_url: str = "postgresql+asyncpg://kb:kb@localhost:5432/kb"

    # Search backend selection
    search_backend: str = "pgvector"  # "pgvector" | "qdrant" | "odoo_orm"

    # pgVector
    pgvector_pool_size: int = 20

    # Qdrant (future)
    qdrant_url: str = ""
    qdrant_collection: str = "kb_chunks"

    # Odoo ORM backend
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_user: str = ""
    odoo_password: str = ""

    # Embeddings
    embedding_provider: str = "openai"  # "openai" | "local"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    openai_api_key: str = ""

    # LLM for query preprocessing
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = 2.0

    # Chunking
    chunk_size: int = 512
    chunk_overlap: int = 64

    # Reranker
    reranker_provider: str = "none"  # "none" | "openai" | "local"
    reranker_model: str = ""  # e.g. "gpt-4o-mini" or "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_top_k: int = 20  # max candidates to rerank

    # RAG answer synthesis
    rag_enabled: bool = False
    rag_llm_provider: str = "openai"   # "openai" | "anthropic" | "ollama" | "none"
    rag_llm_model: str = "gpt-4o-mini"
    rag_max_tokens: int = 300
    rag_temperature: float = 0.3
    rag_system_prompt: str = ""

    # Anthropic (for RAG)
    anthropic_api_key: str = ""

    # Ollama (for RAG)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Query expansion
    query_expansion_provider: str = "static"  # "static" | "llm"

    # Backend weights (JSON map, e.g. {"pgvector": 1.0, "qdrant": 0.9})
    backend_weights: dict[str, float] = {}

    # Conversation context
    conversation_ttl_seconds: int = 1800  # 30 minutes

    # Caching
    embedding_cache_size: int = 5000
    embedding_cache_ttl: int = 3600  # 1 hour
    search_cache_size: int = 500
    search_cache_ttl: int = 120  # 2 minutes

    # Enrichment
    enrichment_enabled: bool = False
    tavily_api_key: str = ""
    google_api_key: str = ""
    google_cse_id: str = ""

    # Filter taxonomy
    filter_taxonomy_path: str = "config/filters.yaml"

    @field_validator("backend_weights", mode="before")
    @classmethod
    def parse_backend_weights(cls, v: Any) -> dict[str, float]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return v

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [k.strip() for k in v.split(",") if k.strip()]
        return v

    model_config = {"env_prefix": "KB_", "env_file": ".env"}


def load_filter_taxonomy(path: str) -> dict[str, Any]:
    """Load filter taxonomy from YAML config file."""
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}
