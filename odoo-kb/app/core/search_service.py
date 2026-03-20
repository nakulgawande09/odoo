"""Search orchestrator: preprocess -> expand -> embed -> fan out -> rerank -> enrich."""
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
    """Coordinates search across preprocessing, backends, and enrichment.

    Phase 2 additions:
    - Conversation context (pronoun resolution, filter carry-over)
    - Query expansion (synonyms/related terms)
    - Semantic reranking (cross-encoder second-stage)
    - Weighted multi-backend scoring
    """

    def __init__(
        self,
        preprocessor: Any,
        embedder: Any,
        backends: list[Any],
        enrichment_providers: list[Any] | None = None,
        reranker: Any | None = None,
        conversation_tracker: Any | None = None,
        query_expander: Any | None = None,
        backend_weights: dict[str, float] | None = None,
    ) -> None:
        self.preprocessor = preprocessor
        self.embedder = embedder
        self.backends = backends
        self.enrichment_providers = enrichment_providers or []
        self.reranker = reranker
        self.conversation_tracker = conversation_tracker
        self.query_expander = query_expander
        # Weight each backend's scores (default 1.0 = equal weight)
        self.backend_weights = backend_weights or {}

    async def search(self, request: SearchRequest) -> SearchResponse:
        start = time.monotonic()

        # 0. Expand query with conversation context (pronoun resolution)
        effective_query = request.query
        context_filters: dict = {}
        if self.conversation_tracker and request.conversation_id:
            effective_query = self.conversation_tracker.expand_query_with_context(
                request.query, request.conversation_id
            )
            context_filters = self.conversation_tracker.get_context_filters(
                request.conversation_id
            )

        # 1. Preprocess query (extract intent + filters)
        processed = await self.preprocessor.process(
            effective_query,
            context={"source": request.source, "conversation_id": request.conversation_id},
        )

        # Merge context filters < extracted filters < caller-provided filters
        merged_filters = {**context_filters, **processed.filters}
        if request.filters:
            merged_filters.update(request.filters)
        processed.filters = merged_filters

        # 2. Query expansion (add synonyms/related terms)
        search_text = processed.search_text
        if self.query_expander:
            search_text = await self.query_expander.expand(search_text)
            processed.search_text = search_text

        # 3. Generate embedding
        processed.embedding = await self.embedder.embed(processed.search_text)

        # 4. Fan out to all backends in parallel
        # Request more results than needed if reranking (reranker needs candidates)
        retrieval_limit = request.limit
        if self.reranker:
            retrieval_limit = max(request.limit * 3, 30)

        backend_tasks = [
            backend.search(processed, limit=retrieval_limit, offset=request.offset)
            for backend in self.backends
        ]
        backend_results = await asyncio.gather(*backend_tasks, return_exceptions=True)

        # 5. Merge results with weighted scoring
        all_results: list[SearchResultItem] = []
        backends_used: list[str] = []
        for backend, result in zip(self.backends, backend_results):
            if isinstance(result, Exception):
                logger.error("Backend %s failed: %s", backend.name, result)
                continue
            backends_used.append(backend.name)

            # Apply backend weight to scores
            weight = self.backend_weights.get(backend.name, 1.0)
            if weight != 1.0:
                result = [
                    item.model_copy(
                        update={
                            "relevance_score": min(item.relevance_score * weight, 1.0)
                        }
                    )
                    for item in result
                ]
            all_results.extend(result)

        # 6. Deduplicate
        unique = self._deduplicate(all_results)

        # 7. Rerank with cross-encoder (if configured)
        if self.reranker and unique:
            unique = await self.reranker.rerank(
                request.query, unique, top_k=request.limit
            )

        # 8. Final ranking and limit
        ranked = self._rank_and_limit(unique, request.limit)

        # 9. Enrich (optional: entity linking, fact-checking)
        enrichment_data = None
        if self.enrichment_providers and ranked:
            enrichment_data, ranked = await self._enrich(request.query, ranked)

        # 10. Record conversation turn (for future context)
        if self.conversation_tracker and request.conversation_id:
            self.conversation_tracker.record_turn(
                request.conversation_id,
                request.query,
                intent=processed.intent,
                filters=processed.filters,
            )

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

    def _deduplicate(self, results: list[SearchResultItem]) -> list[SearchResultItem]:
        """Remove duplicate chunks, keeping the highest-scored version."""
        seen: dict[str, SearchResultItem] = {}
        for item in results:
            key = item.chunk_id or f"{item.document_id}:{item.snippet[:50]}"
            if key not in seen or item.relevance_score > seen[key].relevance_score:
                seen[key] = item
        return list(seen.values())

    def _rank_and_limit(
        self, results: list[SearchResultItem], limit: int
    ) -> list[SearchResultItem]:
        """Sort by relevance score and apply limit."""
        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results[:limit]

    async def _enrich(
        self, query: str, results: list[SearchResultItem]
    ) -> tuple[dict[str, Any] | None, list[SearchResultItem]]:
        """Run enrichment providers. Returns (metadata, possibly-updated results)."""
        enrichment: dict[str, Any] = {}
        current_results = results

        for provider in self.enrichment_providers:
            try:
                enriched = await provider.enrich(query, current_results)
                enrichment[provider.name] = {
                    "status": "ok",
                    "results_count": len(enriched),
                }
                current_results = enriched
            except Exception as e:
                logger.warning("Enrichment provider %s failed: %s", provider.name, e)
                enrichment[provider.name] = {"status": "error", "error": str(e)}

        return (enrichment if enrichment else None), current_results
