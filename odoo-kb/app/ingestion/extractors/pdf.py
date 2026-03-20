"""PDF content extractor."""
from __future__ import annotations

import io


class PdfExtractor:
    @property
    def supported_types(self) -> list[str]:
        return ["application/pdf"]

    async def extract(self, content: bytes | str, content_type: str) -> str:
        if isinstance(content, str):
            content = content.encode("utf-8")

        try:
            import pdfplumber
            pages = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
            return "\n\n".join(pages)
        except ImportError:
            raise ImportError(
                "pdfplumber is required for PDF extraction. "
                "Install it with: pip install pdfplumber"
            )
