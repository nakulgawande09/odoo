"""SQLAlchemy models for document and chunk storage."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class DocumentRecord(Base):
    __tablename__ = "kb_documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False, default="")
    content_type = Column(String, nullable=False, default="text/plain")
    status = Column(
        Enum("pending", "processing", "indexed", "failed", name="document_status"),
        nullable=False,
        default="pending",
    )
    chunks_count = Column(Integer, default=0)
    metadata_ = Column("metadata", JSONB, default=dict)
    tags = Column(JSONB, default=list)
    source_ref = Column(String, nullable=True)
    tenant_id = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    chunks = relationship(
        "ChunkRecord", back_populates="document", cascade="all, delete-orphan"
    )


class ChunkRecord(Base):
    __tablename__ = "kb_chunks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        String, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536))  # dimension set at migration time
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    document = relationship("DocumentRecord", back_populates="chunks")

    __table_args__ = (
        Index("ix_kb_chunks_document_id", "document_id"),
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class QueryLogRecord(Base):
    __tablename__ = "kb_query_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    query = Column(Text, nullable=False)
    processed_query = Column(JSONB, default=dict)
    results_count = Column(Integer, default=0)
    source = Column(String, default="generic")
    conversation_id = Column(String, nullable=True)
    search_time_ms = Column(Integer, default=0)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
