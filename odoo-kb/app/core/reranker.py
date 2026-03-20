"""Semantic reranker using cross-encoder models.

After initial retrieval from vector search (bi-encoder), a cross-encoder
scores each (query, passage) pair jointly for higher accuracy. This is
slower but much more precise — used as a second-stage ranker on the
top-K candidates.
"""
from __future__ import annotations

import logging
from typing import Any

from app.schemas.search import SearchResultItem

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Reranks search results using a cross-encoder model via OpenAI or local model.

    Cross-encoders process query+document pairs together, capturing
    fine-grained semantic relationships that bi-encoders miss.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._provider = getattr(settings, "reranker_provider", "none")
        self._model = getattr(settings, "reranker_model", "")
        self._top_k = getattr(settings, "reranker_top_k", 20)
        self._local_model = None

    async def rerank(
        self,
        query: str,
        results: list[SearchResultItem],
        top_k: int | None = None,
    ) -> list[SearchResultItem]:
        """Rerank results using cross-encoder scoring.

        Args:
            query: The original search query.
            results: Initial retrieval results to rerank.
            top_k: Max results to return after reranking.

        Returns:
            Reranked list of SearchResultItem with updated relevance_score.
        """
        if not results or self._provider == "none":
            return results

        k = top_k or self._top_k
        # Only rerank the top candidates (cross-encoders are slow)
        candidates = results[:k]

        try:
            if self._provider == "openai":
                scores = await self._rerank_openai(query, candidates)
            elif self._provider == "local":
                scores = await self._rerank_local(query, candidates)
            else:
                return results
        except Exception as e:
            logger.warning("Reranking failed, returning original order: %s", e)
            return results

        # Pair results with new scores and sort
        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        reranked = []
        for item, score in scored:
            reranked.append(
                item.model_copy(update={"relevance_score": max(0.0, min(score, 1.0))})
            )

        # Append any results beyond top_k that weren't reranked
        if len(results) > k:
            reranked.extend(results[k:])

        return reranked

    async def _rerank_openai(
        self, query: str, results: list[SearchResultItem]
    ) -> list[float]:
        """Use OpenAI to score query-passage relevance."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._settings.openai_api_key)

        passages = [r.content or r.snippet for r in results]

        prompt = (
            "Rate the relevance of each passage to the query on a scale of 0.0 to 1.0.\n"
            "Return ONLY a JSON array of floats, one per passage, in the same order.\n\n"
            f"Query: {query}\n\n"
        )
        for i, passage in enumerate(passages):
            prompt += f"Passage {i+1}: {passage[:500]}\n\n"

        response = await client.chat.completions.create(
            model=self._settings.reranker_model or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a relevance scoring system. Return only a JSON array of floats."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        import json
        data = json.loads(response.choices[0].message.content)
        # Handle both {"scores": [...]} and direct [...] formats
        if isinstance(data, dict):
            scores = data.get("scores", data.get("relevance", list(data.values())[0]))
        else:
            scores = data

        # Validate and pad/truncate
        scores = [float(s) for s in scores]
        while len(scores) < len(results):
            scores.append(0.0)
        return scores[:len(results)]

    async def _rerank_local(
        self, query: str, results: list[SearchResultItem]
    ) -> list[float]:
        """Use a local cross-encoder model for reranking."""
        import asyncio

        if self._local_model is None:
            from sentence_transformers import CrossEncoder
            model_name = self._model or "cross-encoder/ms-marco-MiniLM-L-6-v2"
            self._local_model = CrossEncoder(model_name)
            logger.info("Loaded local cross-encoder: %s", model_name)

        passages = [r.content or r.snippet for r in results]
        pairs = [[query, p] for p in passages]

        # Run in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        scores = await loop.run_in_executor(
            None, lambda: self._local_model.predict(pairs).tolist()
        )

        # Normalize scores to [0, 1] using sigmoid if needed
        min_s, max_s = min(scores), max(scores)
        if max_s > 1.0 or min_s < 0.0:
            # Normalize to 0-1 range
            span = max_s - min_s if max_s != min_s else 1.0
            scores = [(s - min_s) / span for s in scores]

        return scores


def create_reranker(settings: Any) -> CrossEncoderReranker | None:
    """Factory: returns a reranker if configured, None otherwise."""
    provider = getattr(settings, "reranker_provider", "none")
    if provider == "none":
        return None
    return CrossEncoderReranker(settings)
