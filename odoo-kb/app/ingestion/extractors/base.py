"""Base content extractor interface and extracted content model."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedContent(BaseModel):
    """Result of content extraction."""
    text: str
    title: str | None = None
    metadata: dict = Field(default_factory=dict)
