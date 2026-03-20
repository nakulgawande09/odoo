"""HTML content extractor using BeautifulSoup."""
from __future__ import annotations

import re


class HtmlExtractor:
    @property
    def supported_types(self) -> list[str]:
        return ["text/html"]

    async def extract(self, content: bytes | str, content_type: str) -> str:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            # Remove script and style elements
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
        except ImportError:
            # Fallback: strip tags with regex, removing script/style contents first
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)

        # Collapse whitespace
        lines = (line.strip() for line in text.splitlines())
        return "\n".join(line for line in lines if line)
