from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Stable search input contract. Consumers only need to provide a query string."""
    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    filters: dict[str, str | list[str]] | None = None
    source: str = "generic"  # "livechat", "chatbot", "whatsapp", "agent"
    conversation_id: str | None = None
    tenant_id: str | None = None


class ProcessedQuery(BaseModel):
    """Internal representation after query preprocessing."""
    original_query: str
    intent: str | None = None
    search_text: str
    embedding: list[float] | None = None
    filters: dict[str, str | list[str]] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    tenant_id: str | None = None


class ConnectedEntity(BaseModel):
    """Reference to a related entity (future: graph DB links)."""
    entity_type: str
    entity_id: str
    entity_name: str
    odoo_model: str | None = None
    odoo_id: int | None = None


class SearchResultItem(BaseModel):
    """A single search result with full context."""
    document_id: str
    chunk_id: str | None = None
    title: str
    snippet: str
    content: str | None = None
    relevance_score: float = Field(ge=0.0, le=1.0)
    source_backend: str
    document_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    connected_entities: list[ConnectedEntity] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """Stable search output contract."""
    query: str
    parsed_intent: str | None = None
    parsed_filters: dict[str, Any] = Field(default_factory=dict)
    results: list[SearchResultItem]
    total_count: int
    search_time_ms: float
    backends_used: list[str]
    enrichment: dict[str, Any] | None = None
