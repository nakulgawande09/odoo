"""Initial schema: documents, chunks with pgvector, query log.

Revision ID: 001
Revises:
Create Date: 2026-03-21

This migration matches the schema previously created by Base.metadata.create_all().
For existing deployments, run: alembic stamp 001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create document_status enum
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE document_status AS ENUM ('pending', 'processing', 'indexed', 'failed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    # kb_documents
    op.execute("""
        CREATE TABLE IF NOT EXISTS kb_documents (
            id VARCHAR PRIMARY KEY,
            title VARCHAR NOT NULL DEFAULT '',
            content_type VARCHAR NOT NULL DEFAULT 'text/plain',
            status document_status NOT NULL DEFAULT 'pending',
            chunks_count INTEGER DEFAULT 0,
            metadata JSONB DEFAULT '{}',
            tags JSONB DEFAULT '[]',
            source_ref VARCHAR,
            tenant_id VARCHAR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # kb_chunks (with pgvector column)
    op.execute("""
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id VARCHAR PRIMARY KEY,
            document_id VARCHAR NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding vector(1536),
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Indexes
    op.execute("CREATE INDEX IF NOT EXISTS ix_kb_chunks_document_id ON kb_chunks (document_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding "
        "ON kb_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # kb_query_log
    op.execute("""
        CREATE TABLE IF NOT EXISTS kb_query_log (
            id VARCHAR PRIMARY KEY,
            query TEXT NOT NULL,
            processed_query JSONB DEFAULT '{}',
            results_count INTEGER DEFAULT 0,
            source VARCHAR DEFAULT 'generic',
            conversation_id VARCHAR,
            search_time_ms INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kb_query_log")
    op.execute("DROP TABLE IF EXISTS kb_chunks")
    op.execute("DROP TABLE IF EXISTS kb_documents")
    op.execute("DROP TYPE IF EXISTS document_status")
