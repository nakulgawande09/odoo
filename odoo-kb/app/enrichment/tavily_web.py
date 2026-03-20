"""Tavily web enrichment provider.

Uses the Tavily search API to find external web sources that
corroborate or supplement KB search results. Useful for:
- Fact-checking internal KB content against the web
- Supplementing results when KB coverage is thin
- Adding external references for credibility
"""
from __future__ import annotations

import logging
from typing import Any

from app.schemas.search import SearchResultItem

logger = logging.getLogger(__name__)


class TavilyEnrichmentProvider:
    """Enriches search results with web search context from Tavily."""

    def __init__(self, settings: Any) -> None:
        self._api_key = getattr(settings, "tavily_api_key", "")
        self._enabled = bool(self._api_key)

    @property
    def name(self) -> str:
        return "tavily_web"

    async def enrich(
        self, query: str, results: list[SearchResultItem]
    ) -> list[SearchResultItem]:
        """Add web context to results metadata."""
        if not self._enabled or not results:
            return results

        try:
            web_results = await self._search_web(query)
        except Exception as e:
            logger.warning("Tavily enrichment failed: %s", e)
            return results

        if not web_results:
            return results

        # Attach web references to top results
        enriched = []
        for i, item in enumerate(results):
            if i < 3 and web_results:
                # Add web references to metadata of top 3 results
                updated_metadata = {
                    **item.metadata,
                    "web_references": web_results[:3],
                }
                enriched.append(item.model_copy(update={"metadata": updated_metadata}))
            else:
                enriched.append(item)

        return enriched

    async def _search_web(self, query: str) -> list[dict[str, str]]:
        """Search the web via Tavily API."""
        try:
            from tavily import AsyncTavilyClient
        except ImportError:
            logger.warning("tavily-python not installed, skipping web enrichment")
            return []

        client = AsyncTavilyClient(api_key=self._api_key)
        response = await client.search(
            query=query,
            max_results=5,
            search_depth="basic",
        )

        web_results = []
        for result in response.get("results", []):
            web_results.append({
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "snippet": result.get("content", "")[:200],
            })

        return web_results
