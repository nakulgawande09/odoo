"""RAG answer synthesis: feeds query + retrieved chunks to an LLM for fluent answers.

This service sits between SearchOrchestrator results and the response to callers.
It supports both blocking and streaming generation, with automatic fallback to
raw chunk content if the LLM is unavailable.
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.rag_config import RAGConfig
from app.schemas.search import SearchResultItem

logger = logging.getLogger(__name__)


@dataclass
class SynthesizedAnswer:
    """Result of RAG answer synthesis."""

    answer: str
    model_used: str | None = None
    tokens_used: int | None = None
    synthesis_time_ms: float = 0.0
    fallback_used: bool = False


class AnswerSynthesizer:
    """Synthesizes natural-language answers from search results using an LLM."""

    def __init__(self, llm_provider: Any, default_config: RAGConfig | None = None) -> None:
        self._llm = llm_provider
        self._default_config = default_config or RAGConfig()

    async def synthesize(
        self,
        query: str,
        results: list[SearchResultItem],
        config: RAGConfig | None = None,
        no_answer_message: str = "I don't have that information in our knowledge base.",
    ) -> SynthesizedAnswer:
        """Generate a synthesized answer from search results.

        Falls back to the top chunk's raw content if the LLM call fails.
        """
        cfg = config or self._default_config

        if not results:
            return SynthesizedAnswer(answer=no_answer_message, fallback_used=True)

        messages = self._build_prompt(query, results, cfg)
        start = time.monotonic()

        try:
            answer = await self._llm.generate(
                messages,
                model=cfg.llm_model,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
            )
            elapsed = (time.monotonic() - start) * 1000
            return SynthesizedAnswer(
                answer=answer.strip(),
                model_used=cfg.llm_model,
                synthesis_time_ms=round(elapsed, 2),
                fallback_used=False,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning("RAG synthesis failed (%.0fms), falling back to raw chunk: %s", elapsed, exc)
            fallback = results[0].content or results[0].snippet
            return SynthesizedAnswer(
                answer=fallback,
                synthesis_time_ms=round(elapsed, 2),
                fallback_used=True,
            )

    async def synthesize_stream(
        self,
        query: str,
        results: list[SearchResultItem],
        config: RAGConfig | None = None,
    ) -> AsyncIterator[str]:
        """Stream a synthesized answer token by token."""
        cfg = config or self._default_config

        if not results:
            yield "I don't have that information in our knowledge base."
            return

        messages = self._build_prompt(query, results, cfg)

        try:
            async for token in self._llm.generate_stream(
                messages,
                model=cfg.llm_model,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
            ):
                yield token
        except Exception as exc:
            logger.warning("RAG streaming failed, yielding raw chunk: %s", exc)
            yield results[0].content or results[0].snippet

    def _build_prompt(
        self,
        query: str,
        results: list[SearchResultItem],
        config: RAGConfig,
    ) -> list[dict[str, str]]:
        """Build the LLM messages list from query and context chunks."""
        system_prompt = config.get_system_prompt()

        # Select top chunks within token budget
        chunks = results[: config.max_context_chunks]
        context_parts: list[str] = []
        token_budget = int(config.max_context_tokens * 0.8)  # 80% safety margin
        tokens_used = 0

        for i, result in enumerate(chunks, 1):
            text = result.content or result.snippet
            estimated_tokens = len(text) // 4
            if tokens_used + estimated_tokens > token_budget:
                # Truncate this chunk to fit remaining budget
                remaining_chars = (token_budget - tokens_used) * 4
                if remaining_chars > 100:
                    text = text[:remaining_chars]
                else:
                    break
            context_parts.append(
                f"[Passage {i}] (title: {result.title}, relevance: {result.relevance_score:.2f})\n{text}"
            )
            tokens_used += len(text) // 4

        context_block = "\n\n".join(context_parts)

        user_message = (
            f"Question: {query}\n\n"
            f"Context passages:\n{context_block}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
