"""Recursive text chunker with overlap for context continuity."""
from __future__ import annotations


class RecursiveChunker:
    """Splits text respecting document structure (paragraphs > sentences > words)."""

    def __init__(self, max_chunk_size: int = 512, overlap: int = 64) -> None:
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap

    def chunk(self, text: str, metadata: dict | None = None) -> list[str]:
        if not text.strip():
            return []

        # If text fits in one chunk, return as-is
        if len(text) <= self.max_chunk_size:
            return [text.strip()]

        chunks = []
        # First try splitting by double newlines (paragraphs)
        segments = self._split_recursive(
            text,
            separators=["\n\n", "\n", ". ", " "],
        )

        current_chunk = ""
        for segment in segments:
            if len(current_chunk) + len(segment) <= self.max_chunk_size:
                current_chunk += segment
            else:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                # Start new chunk with overlap from the end of previous
                if self.overlap > 0 and current_chunk:
                    overlap_text = current_chunk[-self.overlap :]
                    current_chunk = overlap_text + segment
                else:
                    current_chunk = segment

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _split_recursive(
        self, text: str, separators: list[str]
    ) -> list[str]:
        """Split text using progressively finer separators."""
        if not separators:
            # Last resort: character-level split
            return [text[i : i + self.max_chunk_size]
                    for i in range(0, len(text), self.max_chunk_size)]

        sep = separators[0]
        parts = text.split(sep)

        result = []
        for part in parts:
            segment = part + sep if part != parts[-1] else part
            if len(segment) <= self.max_chunk_size:
                result.append(segment)
            else:
                # Segment too large, split with next separator
                result.extend(
                    self._split_recursive(segment, separators[1:])
                )
        return result
