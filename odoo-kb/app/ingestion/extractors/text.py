"""Plain text content extractor."""
from __future__ import annotations

from app.ingestion.extractors.base import ExtractedContent


class TextExtractor:
    @property
    def supported_types(self) -> list[str]:
        return ["text/plain"]

    async def extract(self, content: bytes | str, content_type: str) -> str:
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        return content
