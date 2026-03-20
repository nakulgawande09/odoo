from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class IngestRequest(BaseModel):
    """Request to ingest a document into the knowledge base."""
    content: str | None = None
    url: str | None = None
    content_type: str = "text/plain"
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source_ref: str | None = None  # e.g. "product.product:42"
    tenant_id: str | None = None


class DocumentResponse(BaseModel):
    """Document metadata returned by the API."""
    id: str
    title: str
    content_type: str
    status: DocumentStatus
    chunks_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source_ref: str | None = None
    created_at: datetime
    updated_at: datetime


class ChunkData(BaseModel):
    """A chunk of a document ready for indexing."""
    chunk_id: str
    document_id: str
    content: str
    chunk_index: int
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
