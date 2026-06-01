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

    # Gemini
    gemini_api_key: str = ""

    # Embeddings
    embedding_provider: str = "openai"  # "openai" | "local" | "gemini"
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
    reranker_model: str = (
        ""  # e.g. "gpt-4o-mini" or "cross-encoder/ms-marco-MiniLM-L-6-v2"
    )
    reranker_top_k: int = 20  # max candidates to rerank

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

    # TTS
    tts_provider: str = "none"  # "none" | "gemini"
    tts_model: str = "gemini-2.5-flash-preview-tts"
    tts_voice: str = "Kore"
    tts_language: str = "en-US"
    tts_sample_rate: int = 24000

    # STT (Speech-to-Text, used as fallback when browser STT unavailable)
    stt_provider: str = "none"  # "none" | "gemini"
    stt_model: str = "gemini-2.5-flash"
    stt_language: str = "en-US"

    # Answer generation (RAG)
    answer_model: str = "gemini-2.5-flash"
    answer_max_context_chunks: int = 3
    answer_max_tokens: int = 300
    answer_temperature: float = 0.3

    # Gemini Live (real-time audio).
    # Model lives on v1alpha and must support bidiGenerateContent.
    # Override per-agent in Odoo, or via KB_LIVE_MODEL env var.
    live_model: str = "gemini-3.1-flash-live-preview"
    live_voice: str = "Aoede"
    live_preprocessor_model: str = "gemini-3.1-pro-preview"
    live_jwt_secret: str = ""
    live_jwt_ttl_seconds: int = 1800

    # Public URL (for webhook eventUrl callbacks, e.g. ngrok URL)
    public_url: str = ""

    # Vonage
    vonage_api_key: str = ""
    vonage_api_secret: str = ""
    vonage_application_id: str = ""
    vonage_private_key_path: str = ""

    # CRM Integration
    crm_auto_create: bool = True
    crm_min_duration: int = 30  # Min call seconds to create a lead
    crm_analysis_model: str = "gemini-2.5-flash"
    crm_analysis_timeout: float = 15.0

    # Messaging (WhatsApp/SMS)
    vonage_whatsapp_number: str = ""
    vonage_sms_from: str = ""
    notify_team_numbers: list[str] = []
    notify_on_priority: str = "hot,warm"  # Comma-separated priorities
    caller_followup_enabled: bool = True
    caller_followup_channel: str = "whatsapp"  # "whatsapp" | "sms"

    # Filter taxonomy
    filter_taxonomy_path: str = "config/filters.yaml"

    # Domain-specific query expansion synonyms
    synonyms_path: str = "config/synonyms.yaml"

    @field_validator("backend_weights", mode="before")
    @classmethod
    def parse_backend_weights(cls, v: Any) -> dict[str, float]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return v

    @field_validator("notify_team_numbers", mode="before")
    @classmethod
    def parse_team_numbers(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [n.strip() for n in v.split(",") if n.strip()]
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


def load_synonyms(path: str) -> dict[str, list[str]]:
    """Load domain-specific query-expansion synonyms from YAML.

    Returns a mapping of canonical term -> list of synonyms. Empty dict if
    the file is missing or unparseable so the static defaults still apply.
    """
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    except ImportError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, values in data.items():
        if not isinstance(values, list):
            continue
        out[str(key).lower()] = [str(v) for v in values]
    return out
