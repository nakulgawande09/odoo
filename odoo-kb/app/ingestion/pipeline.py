"""Document ingestion pipeline: extract -> chunk -> embed -> index."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.backends.pgvector import PgVectorBackend
from app.core.exceptions import IngestionError
from app.ingestion.chunker import RecursiveChunker
from app.ingestion.extractors.csv_extractor import CsvExtractor
from app.ingestion.extractors.html import HtmlExtractor
from app.ingestion.extractors.pdf import PdfExtractor
from app.ingestion.extractors.text import TextExtractor
from app.schemas.document import ChunkData, DocumentResponse, DocumentStatus, IngestRequest

logger = logging.getLogger(__name__)

# Map MIME types to extractors
_EXTRACTORS: dict[str, Any] = {}


def _get_extractors() -> dict[str, Any]:
    """Lazily build extractor mapping."""
    if not _EXTRACTORS:
        for extractor_cls in [TextExtractor, HtmlExtractor, PdfExtractor, CsvExtractor]:
            instance = extractor_cls()
            for mime in instance.supported_types:
                _EXTRACTORS[mime] = instance
    return _EXTRACTORS


class IngestionPipeline:
    """Orchestrates the full ingestion flow."""

    def __init__(
        self,
        backend: PgVectorBackend,
        embedder: Any,
        chunker: RecursiveChunker | None = None,
    ) -> None:
        self.backend = backend
        self.embedder = embedder
        self.chunker = chunker or RecursiveChunker()

    async def ingest(self, request: IngestRequest) -> DocumentResponse:
        doc_id = str(uuid.uuid4())
        title = request.title or "Untitled"

        # 1. Create document record
        await self.backend.create_document(
            doc_id=doc_id,
            title=title,
            content_type=request.content_type,
            metadata=request.metadata,
            tags=request.tags,
            source_ref=request.source_ref,
            tenant_id=request.tenant_id,
        )

        try:
            # 2. Extract text content
            raw_content = request.content or ""
            if request.url:
                raw_content = await self._fetch_url(request.url)
                if request.content_type == "text/plain":
                    request.content_type = "text/html"

            extractors = _get_extractors()
            extractor = extractors.get(request.content_type)
            if extractor is None:
                # Fallback to text extractor
                extractor = TextExtractor()
            text = await extractor.extract(raw_content, request.content_type)

            if not text.strip():
                await self.backend.update_document_status(
                    doc_id, "indexed", chunks_count=0
                )
                return self._make_response(doc_id, title, request, "indexed", 0)

            # 3. Chunk
            chunks_text = self.chunker.chunk(text, request.metadata)

            # 4. Embed
            embeddings = await self.embedder.embed_batch(chunks_text)

            # 5. Build chunk objects
            chunks = [
                ChunkData(
                    chunk_id=f"{doc_id}_chunk_{i}",
                    document_id=doc_id,
                    content=chunk_text,
                    chunk_index=i,
                    embedding=embedding,
                    metadata={
                        **request.metadata,
                        "title": title,
                        "chunk_total": len(chunks_text),
                    },
                )
                for i, (chunk_text, embedding) in enumerate(
                    zip(chunks_text, embeddings)
                )
            ]

            # 6. Index
            await self.backend.index_chunks(chunks)

            # 7. Update status
            await self.backend.update_document_status(
                doc_id, "indexed", chunks_count=len(chunks)
            )

            logger.info(
                "Ingested document %s: %d chunks", doc_id, len(chunks)
            )
            return self._make_response(
                doc_id, title, request, "indexed", len(chunks)
            )

        except Exception as e:
            await self.backend.update_document_status(doc_id, "failed")
            raise IngestionError(f"Ingestion failed for {doc_id}: {e}") from e

    async def _fetch_url(self, url: str) -> str:
        """Fetch content from a URL."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except ImportError:
            raise IngestionError(
                "httpx required for URL fetching. Install with: pip install httpx"
            )

    def _make_response(
        self,
        doc_id: str,
        title: str,
        request: IngestRequest,
        status: str,
        chunks_count: int,
    ) -> DocumentResponse:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return DocumentResponse(
            id=doc_id,
            title=title,
            content_type=request.content_type,
            status=DocumentStatus(status),
            chunks_count=chunks_count,
            metadata=request.metadata,
            tags=request.tags,
            source_ref=request.source_ref,
            created_at=now,
            updated_at=now,
        )
