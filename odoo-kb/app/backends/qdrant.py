"""Qdrant vector search backend implementation."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.backends.registry import register_backend
from app.schemas.document import ChunkData
from app.schemas.search import ProcessedQuery, SearchResultItem

logger = logging.getLogger(__name__)


@register_backend("qdrant")
class QdrantBackend:
    """Search backend using Qdrant vector database.

    Qdrant provides efficient ANN search with filtering, payload storage,
    and horizontal scaling. Good complement to pgvector for large-scale
    deployments where you want a dedicated vector DB.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._client = None
        self._collection = settings.qdrant_collection

    @property
    def name(self) -> str:
        return "qdrant"

    async def initialize(self) -> None:
        """Connect to Qdrant and ensure collection exists."""
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._client = AsyncQdrantClient(url=self._settings.qdrant_url)

        # Create collection if it doesn't exist
        collections = await self._client.get_collections()
        existing = [c.name for c in collections.collections]

        if self._collection not in existing:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self._settings.embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(
                "Created Qdrant collection %r (dim=%d)",
                self._collection,
                self._settings.embedding_dimensions,
            )
        else:
            logger.info("Using existing Qdrant collection %r", self._collection)

    async def search(
        self,
        query: ProcessedQuery,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResultItem]:
        if query.embedding is None or self._client is None:
            return []

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # Build Qdrant filter from processed query filters
        qdrant_filter = None
        conditions = []
        for key, value in query.filters.items():
            if isinstance(value, str):
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
        if conditions:
            qdrant_filter = Filter(must=conditions)

        results = await self._client.search(
            collection_name=self._collection,
            query_vector=query.embedding,
            query_filter=qdrant_filter,
            limit=limit,
            offset=offset,
            with_payload=True,
        )

        items = []
        for point in results:
            payload = point.payload or {}
            score = max(0.0, min(float(point.score), 1.0))

            items.append(
                SearchResultItem(
                    document_id=payload.get("document_id", ""),
                    chunk_id=str(point.id),
                    title=payload.get("title", ""),
                    snippet=_make_snippet(payload.get("content", "")),
                    content=payload.get("content"),
                    relevance_score=score,
                    source_backend=self.name,
                    document_type=payload.get("document_type"),
                    tags=payload.get("tags", []),
                    metadata=payload.get("metadata", {}),
                    created_at=payload.get("created_at"),
                    updated_at=payload.get("updated_at"),
                )
            )
        return items

    async def index_chunks(self, chunks: list[ChunkData]) -> None:
        if not chunks or self._client is None:
            return

        from qdrant_client.models import PointStruct

        points = []
        for chunk in chunks:
            point_id = chunk.chunk_id or str(uuid.uuid4())
            points.append(
                PointStruct(
                    id=point_id,
                    vector=chunk.embedding,
                    payload={
                        "document_id": chunk.document_id,
                        "content": chunk.content,
                        "chunk_index": chunk.chunk_index,
                        "title": chunk.metadata.get("title", ""),
                        "tags": chunk.metadata.get("tags", []),
                        "document_type": chunk.metadata.get("document_type"),
                        "metadata": chunk.metadata,
                    },
                )
            )

        await self._client.upsert(
            collection_name=self._collection,
            points=points,
        )

    async def delete_document(self, document_id: str) -> None:
        if self._client is None:
            return

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        await self._client.delete(
            collection_name=self._collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id),
                    )
                ]
            ),
        )

    async def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.get_collections()
            return True
        except Exception:
            logger.exception("Qdrant health check failed")
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


def _make_snippet(text: str, max_length: int = 300) -> str:
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_period = truncated.rfind(".")
    if last_period > max_length // 2:
        return truncated[: last_period + 1]
    return truncated.rstrip() + "..."
