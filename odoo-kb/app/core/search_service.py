"""Search orchestrator: preprocess -> expand -> embed -> fan out -> rerank -> enrich -> log."""
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

    Phase 2: conversation context, query expansion, reranking, weighted backends
    Phase 3: query logging, search caching, tenant isolation
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
        query_logger: Any | None = None,
        search_cache: Any | None = None,
    ) -> None:
        self.preprocessor = preprocessor
        self.embedder = embedder
        self.backends = backends
        self.enrichment_providers = enrichment_providers or []
        self.reranker = reranker
        self.conversation_tracker = conversation_tracker
        self.query_expander = query_expander
        self.backend_weights = backend_weights or {}
        self.query_logger = query_logger
        self.search_cache = search_cache

    async def search(self, request: SearchRequest) -> SearchResponse:
        start = time.monotonic()

        # Check search cache first
        if self.search_cache:
            cached = self.search_cache.get(
                request.query, request.filters, request.limit, request.offset
            )
            if cached is not None:
                return cached

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

        # Pass tenant_id through to backends
        processed.tenant_id = request.tenant_id

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

        # 9. Enrich (optional: entity linking, fact-checking, web search)
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

        response = SearchResponse(
            query=request.query,
            parsed_intent=processed.intent,
            parsed_filters=processed.filters,
            results=ranked,
            total_count=len(ranked),
            search_time_ms=round(elapsed_ms, 2),
            backends_used=backends_used,
            enrichment=enrichment_data,
        )

        # 11. Log query (fire-and-forget)
        if self.query_logger:
            asyncio.create_task(
                self.query_logger.log(
                    query=request.query,
                    processed_query={
                        "intent": processed.intent,
                        "filters": processed.filters,
                        "search_text": processed.search_text,
                    },
                    results_count=len(ranked),
                    source=request.source,
                    conversation_id=request.conversation_id,
                    search_time_ms=elapsed_ms,
                    tenant_id=request.tenant_id,
                )
            )

        # 12. Cache response
        if self.search_cache:
            self.search_cache.put(
                request.query, request.filters, request.limit, request.offset, response
            )

        return response

    def _deduplicate(self, results: list[SearchResultItem]) -> list[SearchResultItem]:
        seen: dict[str, SearchResultItem] = {}
        for item in results:
            key = item.chunk_id or f"{item.document_id}:{item.snippet[:50]}"
            if key not in seen or item.relevance_score > seen[key].relevance_score:
                seen[key] = item
        return list(seen.values())

    def _rank_and_limit(
        self, results: list[SearchResultItem], limit: int
    ) -> list[SearchResultItem]:
        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results[:limit]

    async def _enrich(
        self, query: str, results: list[SearchResultItem]
    ) -> tuple[dict[str, Any] | None, list[SearchResultItem]]:
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
