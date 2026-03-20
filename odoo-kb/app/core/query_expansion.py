"""Query expansion: enrich search queries with synonyms and related terms.

Adds terms that the user didn't type but likely mean, improving recall.
Two strategies:
  1. Static synonym map (fast, no API calls)
  2. LLM-based expansion (slower, smarter)
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Domain-agnostic synonym map. Extend per deployment via config.
DEFAULT_SYNONYMS: dict[str, list[str]] = {
    "cancel": ["cancellation", "terminate", "end subscription"],
    "refund": ["money back", "reimburse", "credit"],
    "return": ["send back", "exchange", "return policy"],
    "shipping": ["delivery", "dispatch", "shipment"],
    "price": ["cost", "pricing", "rate", "fee"],
    "discount": ["coupon", "promo", "promotion", "deal", "offer"],
    "install": ["setup", "installation", "configure"],
    "error": ["bug", "issue", "problem", "fault", "failure"],
    "login": ["sign in", "log in", "authenticate"],
    "signup": ["register", "sign up", "create account"],
    "password": ["passcode", "credentials", "PIN"],
    "update": ["upgrade", "patch", "new version"],
    "delete": ["remove", "erase", "uninstall"],
    "invoice": ["bill", "receipt", "statement"],
    "payment": ["pay", "transaction", "charge"],
    "customer": ["client", "buyer", "user"],
    "product": ["item", "goods", "merchandise"],
    "order": ["purchase", "buy", "transaction"],
    "warehouse": ["inventory", "stock", "storage"],
    "employee": ["staff", "worker", "team member"],
}


class StaticQueryExpander:
    """Expands queries using a static synonym dictionary.

    Fast and predictable. Good baseline. The synonym map can be
    loaded from config for domain-specific customization.
    """

    def __init__(self, synonyms: dict[str, list[str]] | None = None) -> None:
        self._synonyms = synonyms or DEFAULT_SYNONYMS
        # Build reverse map for bidirectional lookup
        self._reverse: dict[str, str] = {}
        for key, values in self._synonyms.items():
            for v in values:
                self._reverse[v.lower()] = key

    async def expand(self, query: str, max_additions: int = 3) -> str:
        """Add synonym terms to the query.

        Returns the original query with up to max_additions extra terms
        appended. Only adds terms that aren't already in the query.
        """
        query_lower = query.lower()
        words = set(query_lower.split())
        additions: list[str] = []

        # Check each word against synonym keys
        for word in list(words):
            if word in self._synonyms:
                for syn in self._synonyms[word]:
                    if syn.lower() not in query_lower and len(additions) < max_additions:
                        additions.append(syn)

        # Check multi-word phrases in the query against reverse map
        for phrase, canonical in self._reverse.items():
            if phrase in query_lower and canonical not in query_lower:
                if canonical not in additions and len(additions) < max_additions:
                    additions.append(canonical)

        if additions:
            return f"{query} {' '.join(additions)}"
        return query


class LLMQueryExpander:
    """Expands queries using an LLM for smarter synonym generation.

    Falls back to static expansion on timeout or error.
    """

    def __init__(self, settings: Any, synonyms: dict[str, list[str]] | None = None) -> None:
        self._settings = settings
        self._fallback = StaticQueryExpander(synonyms)

    async def expand(self, query: str, max_additions: int = 3) -> str:
        import asyncio

        try:
            return await asyncio.wait_for(
                self._llm_expand(query, max_additions),
                timeout=self._settings.llm_timeout,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("LLM query expansion failed, using static: %s", e)
            return await self._fallback.expand(query, max_additions)

    async def _llm_expand(self, query: str, max_additions: int) -> str:
        from openai import AsyncOpenAI
        import json

        client = AsyncOpenAI(api_key=self._settings.openai_api_key)

        response = await client.chat.completions.create(
            model=self._settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Given a search query, suggest up to {max_additions} additional "
                        "search terms (synonyms or closely related concepts) that would "
                        "help find relevant results. Return JSON: "
                        '{"expanded_query": "original query plus new terms"}'
                    ),
                },
                {"role": "user", "content": query},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        data = json.loads(response.choices[0].message.content)
        return data.get("expanded_query", query)


def create_query_expander(
    settings: Any, synonyms: dict[str, list[str]] | None = None
) -> StaticQueryExpander | LLMQueryExpander:
    """Factory: returns LLM expander if configured, else static."""
    if (
        getattr(settings, "query_expansion_provider", "static") == "llm"
        and getattr(settings, "openai_api_key", "")
    ):
        return LLMQueryExpander(settings, synonyms)
    return StaticQueryExpander(synonyms)
