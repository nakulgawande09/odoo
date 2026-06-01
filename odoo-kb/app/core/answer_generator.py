"""RAG answer generation: synthesise a natural-language answer from retrieved chunks.

Takes the user query plus the top-K search results and calls an LLM
to produce a concise, conversational answer grounded in the KB context.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import AnswerGenerationError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a helpful voice assistant{agent_clause}.\n"
    "Answer the user's question using ONLY the context provided below.\n"
    "If the context doesn't contain relevant information for the question, say so honestly.\n"
    "If the user sends a greeting (like 'hi', 'hello'), respond with a friendly greeting "
    "and ask how you can help -- do NOT dump unrelated information.\n"
    "Keep your answer concise and conversational (suitable for text-to-speech).\n"
    "Do not mention 'the context' or 'the document' -- speak naturally as if you know the answer."
)

CONTEXT_TEMPLATE = "--- Source: {title} ---\n{content}\n"


@dataclass(frozen=True)
class GeneratedAnswer:
    """Result of RAG answer generation."""

    text: str
    confidence: float
    sources_used: int
    model: str


class AnswerGenerator:
    """Generates answers from retrieved KB chunks using an LLM."""

    def __init__(self, settings: Any) -> None:
        self._api_key = settings.gemini_api_key
        self._model = getattr(settings, "answer_model", "gemini-2.0-flash")
        self._max_chunks = getattr(settings, "answer_max_context_chunks", 3)
        self._max_tokens = getattr(settings, "answer_max_tokens", 300)
        self._temperature = getattr(settings, "answer_temperature", 0.3)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai

                self._client = genai.Client(api_key=self._api_key)
            except ImportError:
                raise AnswerGenerationError(
                    "google-genai package required. Install with: pip install google-genai"
                )
        return self._client

    async def generate(
        self,
        query: str,
        results: list[Any],
        agent_name: str | None = None,
        config: Any | None = None,
    ) -> GeneratedAnswer:
        """Synthesise an answer from search results using an LLM.

        Args:
            query: The original user question.
            results: Search result items (need .title, .content/.snippet, .relevance_score).
            agent_name: Optional agent name for personalised system prompt.
            config: Optional VoiceAgentConfig for fallback messages.

        Returns:
            GeneratedAnswer with synthesised text and metadata.
        """
        if not results:
            fallback = "I couldn't find information about that in our knowledge base."
            if config:
                fallback = getattr(config, "no_answer_message", fallback)
            return GeneratedAnswer(
                text=fallback, confidence=0.0, sources_used=0, model=self._model
            )

        top_results = results[: self._max_chunks]
        avg_confidence = sum(r.relevance_score for r in top_results) / len(top_results)

        # Check confidence threshold
        if config and avg_confidence < getattr(config, "confidence_threshold", 0.3):
            fallback = "I'm not fully confident in my answer. Let me transfer you to someone who can help better."
            if config:
                fallback = getattr(config, "low_confidence_message", fallback)
            return GeneratedAnswer(
                text=fallback,
                confidence=avg_confidence,
                sources_used=len(top_results),
                model=self._model,
            )

        # Build context from chunks
        context_parts = []
        for r in top_results:
            content = getattr(r, "content", None) or getattr(r, "snippet", "")
            title = getattr(r, "title", "Untitled")
            context_parts.append(
                CONTEXT_TEMPLATE.format(title=title, content=content)
            )
        context_block = "\n".join(context_parts)

        # Build prompt
        agent_clause = f" for {agent_name}" if agent_name else ""
        system = SYSTEM_PROMPT.format(agent_clause=agent_clause)
        user_prompt = f"Context:\n{context_block}\n\nUser question: {query}\n\nAnswer:"

        client = self._get_client()

        try:
            from google.genai import types

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=self._model,
                    contents=[
                        types.Content(
                            role="user",
                            parts=[types.Part(text=user_prompt)],
                        ),
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        max_output_tokens=self._max_tokens,
                        temperature=self._temperature,
                    ),
                ),
            )

            answer_text = response.text.strip() if response.text else ""
            logger.info(
                "RAG generated answer (model=%s, chunks=%d, query=%r): %s",
                self._model, len(top_results), query[:50], answer_text[:100],
            )
            if not answer_text:
                raise AnswerGenerationError("LLM returned empty response")

            # Truncate for TTS if config specifies max length
            max_len = getattr(config, "max_answer_length", 500) if config else 500
            if len(answer_text) > max_len:
                truncated = answer_text[:max_len]
                last_period = truncated.rfind(".")
                if last_period > max_len // 2:
                    answer_text = truncated[: last_period + 1]
                else:
                    answer_text = truncated + "..."

            return GeneratedAnswer(
                text=answer_text,
                confidence=avg_confidence,
                sources_used=len(top_results),
                model=self._model,
            )

        except AnswerGenerationError:
            raise
        except Exception as e:
            raise AnswerGenerationError(
                f"Answer generation failed: {e}"
            ) from e


def create_answer_generator(settings: Any) -> AnswerGenerator | None:
    """Factory: returns an AnswerGenerator if Gemini is configured."""
    if not getattr(settings, "gemini_api_key", ""):
        logger.warning("No gemini_api_key configured -- answer generation disabled")
        return None
    return AnswerGenerator(settings)
