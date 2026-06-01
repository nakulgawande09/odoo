"""pgVector search backend implementation."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.backends.registry import register_backend
from app.models.document import Base, ChunkRecord, DocumentRecord
from app.schemas.document import ChunkData, DocumentStatus
from app.schemas.search import ProcessedQuery, SearchResultItem

logger = logging.getLogger(__name__)


@register_backend("pgvector")
class PgVectorBackend:
    """Search backend using PostgreSQL with pgvector extension."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._engine = create_async_engine(
            settings.database_url,
            pool_size=settings.pgvector_pool_size,
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    @property
    def name(self) -> str:
        return "pgvector"

    async def initialize(self) -> None:
        """Create tables, enable pgvector, and ensure FTS column + GIN index.

        Idempotent migration: adds a generated `content_tsv` tsvector column
        and a GIN index on it so the hybrid (vector + BM25) search in
        `search()` can keyword-match entity-heavy queries. Requires PG 12+.
        """
        async with self._engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            # Add full-text search column (idempotent; PG 12+ generated column).
            await conn.execute(
                text(
                    "ALTER TABLE kb_chunks "
                    "ADD COLUMN IF NOT EXISTS content_tsv tsvector "
                    "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kb_chunks_content_tsv "
                    "ON kb_chunks USING gin (content_tsv)"
                )
            )
        logger.info("pgVector backend initialized (with FTS column + GIN index)")

    async def search(
        self,
        query: ProcessedQuery,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResultItem]:
        """Hybrid retrieval: vector cosine + BM25 fused with Reciprocal Rank Fusion.

        Vector handles semantic similarity; BM25 (Postgres FTS via tsvector +
        ts_rank_cd) handles exact / rare-token matching (proper nouns, acronyms,
        entity names). RRF combines them robustly without score-scale issues.

        Constants:
          - candidates: pulls 5x the requested limit from each subindex so RRF
            has enough headroom to rerank.
          - RRF_K (60): standard RRF dampening constant from the literature.
        """
        if query.embedding is None:
            return []

        embedding_str = f"[{','.join(str(x) for x in query.embedding)}]"
        candidates = max(limit * 5, 30)
        bm25_text = (query.search_text or "").strip()

        # Tenant isolation: applied to BOTH vector and BM25 sub-queries.
        tenant_clause = ""
        params: dict = {
            "embedding": embedding_str,
            "candidates": candidates,
            "limit": limit,
            "offset": offset,
            "query_text": bm25_text,
        }
        if query.tenant_id:
            tenant_clause = "AND (d.tenant_id = :tenant_id OR d.tenant_id IS NULL)"
            params["tenant_id"] = query.tenant_id

        # If we have no text to BM25-search (rare), fall back to vector-only.
        bm25_cte = ""
        union_bm25 = ""
        if bm25_text:
            bm25_cte = f""",
                bm25 AS (
                    SELECT
                        c.id,
                        ROW_NUMBER() OVER (
                            ORDER BY ts_rank_cd(
                                c.content_tsv,
                                websearch_to_tsquery('english', :query_text)
                            ) DESC
                        ) AS rank
                    FROM kb_chunks c
                    JOIN kb_documents d ON d.id = c.document_id
                    WHERE d.status = 'indexed'
                      AND c.content_tsv @@ websearch_to_tsquery('english', :query_text)
                      {tenant_clause}
                    LIMIT :candidates
                )
            """
            union_bm25 = "UNION ALL SELECT id, 1.0 / (60 + rank) AS rrf_score FROM bm25"

        async with self._session_factory() as session:
            stmt = text(f"""
                WITH vec AS (
                    SELECT
                        c.id,
                        ROW_NUMBER() OVER (
                            ORDER BY c.embedding <=> CAST(:embedding AS vector)
                        ) AS rank
                    FROM kb_chunks c
                    JOIN kb_documents d ON d.id = c.document_id
                    WHERE d.status = 'indexed'
                    {tenant_clause}
                    ORDER BY c.embedding <=> CAST(:embedding AS vector)
                    LIMIT :candidates
                ){bm25_cte},
                fused AS (
                    SELECT id, SUM(rrf_score) AS score
                    FROM (
                        SELECT id, 1.0 / (60 + rank) AS rrf_score FROM vec
                        {union_bm25}
                    ) u
                    GROUP BY id
                )
                SELECT
                    c.id AS chunk_id,
                    c.document_id,
                    c.content,
                    c.chunk_index,
                    c.metadata,
                    d.title,
                    d.content_type,
                    d.tags,
                    d.metadata AS doc_metadata,
                    d.created_at,
                    d.updated_at,
                    f.score AS similarity
                FROM fused f
                JOIN kb_chunks c ON c.id = f.id
                JOIN kb_documents d ON d.id = c.document_id
                ORDER BY f.score DESC
                LIMIT :limit OFFSET :offset
            """)
            result = await session.execute(stmt, params)
            rows = result.mappings().all()

        # RRF scores are on a tiny absolute scale (~0.03). Per-query normalize
        # by the max so downstream code (reranker, confidence threshold) sees
        # values in [0, 1] -- the top result lands at 1.0 by construction.
        max_score = max((float(r["similarity"]) for r in rows), default=1.0)
        if max_score <= 0:
            max_score = 1.0

        items = []
        for row in rows:
            score = float(row["similarity"]) / max_score
            if score < 0:
                score = 0.0

            items.append(
                SearchResultItem(
                    document_id=row["document_id"],
                    chunk_id=row["chunk_id"],
                    title=row["title"] or "",
                    snippet=_make_snippet(row["content"]),
                    content=row["content"],
                    relevance_score=min(score, 1.0),
                    source_backend=self.name,
                    document_type=row["doc_metadata"].get("document_type")
                    if row["doc_metadata"]
                    else None,
                    tags=row["tags"] or [],
                    metadata={
                        **(row["metadata"] or {}),
                        **(row["doc_metadata"] or {}),
                        "chunk_index": row["chunk_index"],
                    },
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return items

    async def index_chunks(self, chunks: list[ChunkData]) -> None:
        if not chunks:
            return
        async with self._session_factory() as session:
            async with session.begin():
                for chunk in chunks:
                    record = ChunkRecord(
                        id=chunk.chunk_id or str(uuid.uuid4()),
                        document_id=chunk.document_id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        embedding=chunk.embedding,
                        metadata_=chunk.metadata,
                    )
                    session.add(record)

    async def delete_document(self, document_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(ChunkRecord).where(
                        ChunkRecord.document_id == document_id
                    )
                )

    async def create_document(
        self,
        doc_id: str,
        title: str,
        content_type: str,
        metadata: dict,
        tags: list[str],
        source_ref: str | None = None,
        tenant_id: str | None = None,
    ) -> DocumentRecord:
        async with self._session_factory() as session:
            async with session.begin():
                record = DocumentRecord(
                    id=doc_id,
                    title=title,
                    content_type=content_type,
                    status="pending",
                    metadata_=metadata,
                    tags=tags,
                    source_ref=source_ref,
                    tenant_id=tenant_id,
                )
                session.add(record)
            return record

    async def update_document_status(
        self, doc_id: str, status: str, chunks_count: int = 0
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(DocumentRecord).where(DocumentRecord.id == doc_id)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    doc.status = status
                    doc.chunks_count = chunks_count

    async def get_document(self, doc_id: str) -> DocumentRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentRecord).where(DocumentRecord.id == doc_id)
            )
            return result.scalar_one_or_none()

    async def list_documents(
        self, limit: int = 20, offset: int = 0
    ) -> list[DocumentRecord]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentRecord)
                .order_by(DocumentRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all())

    async def health_check(self) -> bool:
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.exception("pgVector health check failed")
            return False

    async def close(self) -> None:
        await self._engine.dispose()


def _make_snippet(text: str, max_length: int = 300) -> str:
    """Create a snippet from chunk content."""
    if len(text) <= max_length:
        return text
    # Try to break at a sentence boundary
    truncated = text[:max_length]
    last_period = truncated.rfind(".")
    if last_period > max_length // 2:
        return truncated[: last_period + 1]
    return truncated.rstrip() + "..."
