"""Search orchestrator: preprocess -> embed -> fan out to backends -> rank -> enrich."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.schemas.search import (
    ProcessedQuery,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)

logger = logging.getLogger(__name__)


class SearchOrchestrator:
    """Coordinates search across preprocessing, backends, and enrichment."""

    def __init__(
        self,
        preprocessor: Any,
        embedder: Any,
        backends: list[Any],
        enrichment_providers: list[Any] | None = None,
    ) -> None:
        self.preprocessor = preprocessor
        self.embedder = embedder
        self.backends = backends
        self.enrichment_providers = enrichment_providers or []

    async def search(self, request: SearchRequest) -> SearchResponse:
        start = time.monotonic()

        # 1. Preprocess query (extract intent + filters)
        processed = await self.preprocessor.process(
            request.query,
            context={"source": request.source, "conversation_id": request.conversation_id},
        )

        # Merge caller-provided filters with extracted filters
        if request.filters:
            processed.filters = {**processed.filters, **request.filters}

        # 2. Generate embedding
        processed.embedding = await self.embedder.embed(processed.search_text)

        # 3. Fan out to all backends in parallel
        backend_tasks = [
            backend.search(processed, limit=request.limit, offset=request.offset)
            for backend in self.backends
        ]
        backend_results = await asyncio.gather(*backend_tasks, return_exceptions=True)

        # 4. Merge results from all backends
        all_results: list[SearchResultItem] = []
        backends_used: list[str] = []
        for backend, result in zip(self.backends, backend_results):
            if isinstance(result, Exception):
                logger.error("Backend %s failed: %s", backend.name, result)
                continue
            backends_used.append(backend.name)
            all_results.extend(result)

        # 5. Deduplicate and rank
        ranked = self._rank_and_deduplicate(all_results, request.limit)

        # 6. Enrich (optional, for fact-checking)
        enrichment_data = None
        if self.enrichment_providers and ranked:
            enrichment_data = await self._enrich(request.query, ranked)

        elapsed_ms = (time.monotonic() - start) * 1000

        return SearchResponse(
            query=request.query,
            parsed_intent=processed.intent,
            parsed_filters=processed.filters,
            results=ranked,
            total_count=len(ranked),
            search_time_ms=round(elapsed_ms, 2),
            backends_used=backends_used,
            enrichment=enrichment_data,
        )

    def _rank_and_deduplicate(
        self, results: list[SearchResultItem], limit: int
    ) -> list[SearchResultItem]:
        """Remove duplicates and sort by relevance score."""
        seen_chunks: set[str] = set()
        unique: list[SearchResultItem] = []

        for item in results:
            key = item.chunk_id or f"{item.document_id}:{item.snippet[:50]}"
            if key not in seen_chunks:
                seen_chunks.add(key)
                unique.append(item)

        unique.sort(key=lambda x: x.relevance_score, reverse=True)
        return unique[:limit]

    async def _enrich(
        self, query: str, results: list[SearchResultItem]
    ) -> dict[str, Any] | None:
        """Run enrichment providers for fact-checking."""
        enrichment: dict[str, Any] = {}
        for provider in self.enrichment_providers:
            try:
                enriched = await provider.enrich(query, results)
                enrichment[provider.name] = {
                    "status": "ok",
                    "results_count": len(enriched),
                }
            except Exception as e:
                logger.warning("Enrichment provider %s failed: %s", provider.name, e)
                enrichment[provider.name] = {"status": "error", "error": str(e)}
        return enrichment if enrichment else None
