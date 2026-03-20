"""Core interfaces for the Knowledge Base service.

These Protocol classes define the stable contract. Implementations can be
swapped without breaking consumers. Use structural subtyping — concrete
classes do NOT need to inherit from these; they just need to match the
method signatures.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas.document import ChunkData
from app.schemas.search import ProcessedQuery, SearchResultItem


@runtime_checkable
class SearchBackend(Protocol):
    """Strategy interface for search backends (pgVector, Qdrant, Odoo ORM, etc.)."""

    @property
    def name(self) -> str:
        """Unique backend identifier."""
        ...

    async def search(
        self,
        query: ProcessedQuery,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResultItem]:
        ...

    async def index_chunks(self, chunks: list[ChunkData]) -> None:
        ...

    async def delete_document(self, document_id: str) -> None:
        ...

    async def health_check(self) -> bool:
        ...


@runtime_checkable
class QueryPreprocessor(Protocol):
    """Processes raw query strings into structured queries with intent and filters."""

    async def process(self, raw_query: str, context: dict | None = None) -> ProcessedQuery:
        ...


@runtime_checkable
class Embedder(Protocol):
    """Generates vector embeddings from text."""

    @property
    def dimensions(self) -> int:
        ...

    async def embed(self, text: str) -> list[float]:
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


@runtime_checkable
class ContentExtractor(Protocol):
    """Extracts text from various content formats."""

    @property
    def supported_types(self) -> list[str]:
        ...

    async def extract(self, content: bytes | str, content_type: str) -> str:
        ...


@runtime_checkable
class Chunker(Protocol):
    """Splits text into chunks suitable for embedding and indexing."""

    def chunk(self, text: str, metadata: dict | None = None) -> list[str]:
        ...


@runtime_checkable
class EnrichmentProvider(Protocol):
    """Enriches search results with external data (entity linking, fact-checking, etc.)."""

    @property
    def name(self) -> str:
        ...

    async def enrich(
        self, query: str, results: list[SearchResultItem]
    ) -> list[SearchResultItem]:
        ...


@runtime_checkable
class Reranker(Protocol):
    """Second-stage reranker for improving result relevance."""

    async def rerank(
        self,
        query: str,
        results: list[SearchResultItem],
        top_k: int | None = None,
    ) -> list[SearchResultItem]:
        ...


@runtime_checkable
class QueryExpander(Protocol):
    """Expands queries with synonyms and related terms."""

    async def expand(self, query: str, max_additions: int = 3) -> str:
        ...
