"""Function-calling tools exposed to Gemini Live.

This module is the only place where query preprocessing logic lives.
Gemini Live just calls `search_kb(query=...)`; the handler enriches
the query with conversation context, normalizes language, classifies
intent, and runs it through the existing SearchOrchestrator.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.schemas.search import SearchRequest

logger = logging.getLogger(__name__)


_PREPROCESS_SYSTEM_PROMPT = (
    "You normalize voice-call utterances for knowledge-base retrieval.\n"
    "Given the conversation history and the latest caller utterance, return JSON:\n"
    '  {\n'
    '    "rewritten_query": "<self-contained question, no pronouns, in the KB language>",\n'
    '    "intent": "factual" | "small_talk" | "escalation" | "goodbye" | "other",\n'
    '    "language": "<BCP-47 code of the original utterance>",\n'
    '    "filters": {<optional metadata filters extracted from the query>}\n'
    '  }\n'
    "Rules:\n"
    " - Resolve pronouns and elliptical references using history (e.g. 'how much is it?' → "
    "'how much is the X?').\n"
    " - Translate the question into the KB's canonical language (assume English unless "
    "the system tells you otherwise).\n"
    " - For small_talk, escalation, or goodbye, set rewritten_query to an empty string.\n"
    " - Do NOT answer the question; only rewrite it.\n"
    "Respond with ONLY the JSON object — no prose, no markdown."
)


@dataclass(frozen=True)
class PreprocessedQuery:
    rewritten_query: str
    intent: str
    language: str
    filters: dict[str, Any]


class QueryPreprocessor:
    """Cheap Gemini-Flash text pass that turns voice fragments into KB queries."""

    def __init__(self, *, client: Any, model: str, kb_language: str = "en") -> None:
        self._client = client
        self._model = model
        self._kb_language = kb_language

    async def preprocess(
        self,
        query: str,
        *,
        conversation_history: list[dict],
        language_hint: str | None = None,
    ) -> PreprocessedQuery:
        """Return a self-contained, KB-language version of the query.

        On any failure, falls back to the raw query so retrieval still
        runs — better to retrieve on the unrewritten text than to drop
        the turn.
        """
        history_blob = _format_history(conversation_history)
        language_clause = (
            f"\nThe KB's canonical language is '{self._kb_language}'. "
            f"The caller is most recently speaking '{language_hint or 'unknown'}'."
        )
        user_prompt = (
            f"{language_clause}\n\n"
            f"Conversation so far:\n{history_blob}\n\n"
            f"Latest caller utterance: {query}\n\n"
            f"Respond with the JSON object."
        )

        try:
            from google.genai import types

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model=self._model,
                    contents=[
                        types.Content(
                            role="user", parts=[types.Part(text=user_prompt)],
                        ),
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=_PREPROCESS_SYSTEM_PROMPT,
                        max_output_tokens=200,
                        temperature=0.0,
                        response_mime_type="application/json",
                    ),
                ),
            )
            text = (response.text or "").strip()
            data = json.loads(text)
            return PreprocessedQuery(
                rewritten_query=str(data.get("rewritten_query") or "").strip(),
                intent=str(data.get("intent") or "factual"),
                language=str(data.get("language") or language_hint or "en"),
                filters=dict(data.get("filters") or {}),
            )
        except Exception as exc:  # noqa: BLE001 — fail open to raw query
            logger.warning("query preprocessing failed (%s); using raw query", exc)
            return PreprocessedQuery(
                rewritten_query=query,
                intent="factual",
                language=language_hint or self._kb_language,
                filters={},
            )


def _format_history(history: list[dict]) -> str:
    """Render the last ~6 turns as a compact transcript for the preprocessor."""
    if not history:
        return "(no prior turns)"
    recent = history[-6:]
    return "\n".join(
        f"{turn.get('role', '?').capitalize()}: {turn.get('text', '')[:200]}"
        for turn in recent
    )


# ── search_kb tool handler ───────────────────────────────────


async def search_kb(
    query: str,
    agent_id: int,
    language: str | None,
    conversation_history: list[dict],
    *,
    preprocessor: QueryPreprocessor,
    orchestrator: Any,
    voice_agent_store: Any,
    call_uuid: str | None = None,
) -> dict:
    """Preprocess the query, retrieve from the KB, return a compact result.

    The shape returned here is what the Gemini Live model sees as the
    `search_kb` function response, so it must be self-describing JSON.
    """
    cfg = voice_agent_store.get(agent_id) if voice_agent_store else None
    max_results = getattr(cfg, "max_results", None) or 3
    confidence_threshold = getattr(cfg, "confidence_threshold", None) or 0.3

    pre = await preprocessor.preprocess(
        query, conversation_history=conversation_history,
        language_hint=language,
    )

    # Non-factual intents short-circuit retrieval — let the live model
    # handle small talk / goodbyes via its system instruction.
    if pre.intent in {"small_talk", "goodbye"} or not pre.rewritten_query:
        return {
            "intent": pre.intent,
            "rewritten_query": pre.rewritten_query,
            "language": pre.language,
            "chunks": [],
            "instruction": "No KB lookup needed — answer conversationally per system rules.",
        }

    if pre.intent == "escalation":
        return {
            "intent": "escalation",
            "rewritten_query": pre.rewritten_query,
            "language": pre.language,
            "chunks": [],
            "instruction": "Caller is requesting a human. Use the escalation message and end the call.",
        }

    # conversation_id MUST be per-call so the orchestrator's ConversationTracker
    # segments turn history correctly. Falling back to agent-id was a bug --
    # concurrent calls to the same agent would bleed context into each other.
    request = SearchRequest(
        query=pre.rewritten_query,
        limit=max_results,
        filters=pre.filters or None,
        source="voice_live",
        conversation_id=call_uuid or f"agent-{agent_id}",
    )

    try:
        resp = await orchestrator.search(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_kb orchestrator failed: %s", exc)
        return {
            "error": str(exc),
            "rewritten_query": pre.rewritten_query,
            "chunks": [],
        }

    chunks = []
    for item in resp.results[:max_results]:
        chunks.append({
            "title": item.title,
            "snippet": (item.content or item.snippet or "")[:1200],
            "score": round(float(item.relevance_score), 4),
            "source": item.source_backend,
            "doc_id": item.document_id,
        })

    avg_score = (
        sum(c["score"] for c in chunks) / len(chunks) if chunks else 0.0
    )

    instruction = (
        "Use these chunks as your only source of truth. Quote facts from them; "
        "do not infer beyond the snippet text."
    )
    if not chunks:
        instruction = (
            "No relevant KB content was found. Use the no-answer fallback "
            "message and offer to escalate."
        )
    elif avg_score < confidence_threshold:
        instruction = (
            "The retrieved chunks have low confidence. Use the low-confidence "
            "fallback message and offer to escalate."
        )

    return {
        "intent": pre.intent,
        "rewritten_query": pre.rewritten_query,
        "language": pre.language,
        "chunks": chunks,
        "avg_confidence": round(avg_score, 4),
        "confidence_threshold": confidence_threshold,
        "instruction": instruction,
    }
