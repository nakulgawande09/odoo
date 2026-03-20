"""Query preprocessing: normalize, extract intent, extract filters."""
from __future__ import annotations

import logging
import re
from typing import Any

from app.schemas.search import ProcessedQuery

logger = logging.getLogger(__name__)


class RuleBasedPreprocessor:
    """Rule-based query preprocessor.

    Extracts intent and filters using keyword/pattern matching.
    Fast, no external API calls. Can be replaced with an LLM-based
    preprocessor that conforms to the same QueryPreprocessor protocol.
    """

    # Intent patterns: (regex, intent_name)
    INTENT_PATTERNS: list[tuple[str, str]] = [
        (r"\b(how to|how do|steps to|guide)\b", "how_to"),
        (r"\b(what is|what are|define|meaning of)\b", "definition"),
        (r"\b(fix|error|issue|problem|bug|broken|not working)\b", "troubleshooting"),
        (r"\b(compare|vs|versus|difference between)\b", "comparison"),
        (r"\b(return|refund|exchange|cancel)\b", "return_policy"),
        (r"\b(price|cost|pricing|plan)\b", "pricing"),
    ]

    def __init__(self, filter_taxonomy: dict[str, Any] | None = None) -> None:
        self._taxonomy = filter_taxonomy or {}
        self._build_filter_patterns()

    def _build_filter_patterns(self) -> None:
        """Build regex patterns from filter taxonomy."""
        self._filter_values: dict[str, dict[str, str]] = {}
        categories = self._taxonomy.get("filter_categories", {})
        for filter_name, config in categories.items():
            values = config.get("values", []) + config.get("options", [])
            aliases = config.get("aliases", {})
            mapping = {}
            for v in values:
                # Map the value itself and any aliases
                mapping[v.lower().replace("_", " ")] = v
            for alias, canonical in aliases.items():
                mapping[alias.lower()] = canonical
            self._filter_values[filter_name] = mapping

    async def process(
        self, raw_query: str, context: dict | None = None
    ) -> ProcessedQuery:
        cleaned = self._normalize(raw_query)
        intent = self._extract_intent(cleaned)
        filters, remaining = self._extract_filters(cleaned)

        return ProcessedQuery(
            original_query=raw_query,
            intent=intent,
            search_text=remaining or cleaned,
            filters=filters,
            confidence=0.6 if intent else 0.3,
        )

    def _normalize(self, query: str) -> str:
        return " ".join(query.strip().lower().split())

    def _extract_intent(self, query: str) -> str | None:
        for pattern, intent in self.INTENT_PATTERNS:
            if re.search(pattern, query, re.IGNORECASE):
                return intent
        return "informational"  # default

    def _extract_filters(
        self, query: str
    ) -> tuple[dict[str, str | list[str]], str]:
        """Extract structured filters from natural language query."""
        filters: dict[str, str | list[str]] = {}
        remaining = query

        for filter_name, value_map in self._filter_values.items():
            for text_match, canonical_value in sorted(
                value_map.items(), key=lambda x: -len(x[0])
            ):
                if text_match in remaining.lower():
                    filters[filter_name] = canonical_value
                    # Remove matched text from query
                    remaining = re.sub(
                        re.escape(text_match), "", remaining, flags=re.IGNORECASE
                    ).strip()
                    break

        # Clean up remaining query
        remaining = " ".join(remaining.split())
        remaining = re.sub(r"\b(for|in|from|during|about)\b\s*$", "", remaining).strip()

        return filters, remaining


class LLMPreprocessor:
    """LLM-based query preprocessor with hard timeout fallback.

    Uses a language model to extract intent and filters from natural
    language queries. Falls back to the raw query if LLM times out.
    """

    def __init__(
        self,
        settings: Any,
        filter_taxonomy: dict[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        self._taxonomy = filter_taxonomy or {}
        self._fallback = RuleBasedPreprocessor(filter_taxonomy)

    async def process(
        self, raw_query: str, context: dict | None = None
    ) -> ProcessedQuery:
        import asyncio

        try:
            result = await asyncio.wait_for(
                self._llm_process(raw_query, context),
                timeout=self._settings.llm_timeout,
            )
            return result
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("LLM preprocessing failed, falling back: %s", e)
            return await self._fallback.process(raw_query, context)

    async def _llm_process(
        self, raw_query: str, context: dict | None
    ) -> ProcessedQuery:
        """Call LLM to extract intent and filters."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self._settings.openai_api_key)

            categories_desc = "\n".join(
                f"- {name}: {config}"
                for name, config in self._taxonomy.get("filter_categories", {}).items()
            )

            response = await client.chat.completions.create(
                model=self._settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract the search intent and filters from the user query.\n"
                            f"Available filter categories:\n{categories_desc}\n\n"
                            "Respond in JSON format:\n"
                            '{"intent": "string", "search_text": "cleaned query", '
                            '"filters": {"category_name": "value"}}'
                        ),
                    },
                    {"role": "user", "content": raw_query},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )

            import json
            data = json.loads(response.choices[0].message.content)

            return ProcessedQuery(
                original_query=raw_query,
                intent=data.get("intent"),
                search_text=data.get("search_text", raw_query),
                filters=data.get("filters", {}),
                confidence=0.9,
            )
        except ImportError:
            return await self._fallback.process(raw_query, context)


def create_preprocessor(
    settings: Any, filter_taxonomy: dict[str, Any] | None = None
) -> RuleBasedPreprocessor | LLMPreprocessor:
    """Factory to create the configured preprocessor."""
    if getattr(settings, "llm_provider", None) == "openai" and settings.openai_api_key:
        return LLMPreprocessor(settings, filter_taxonomy)
    return RuleBasedPreprocessor(filter_taxonomy)
