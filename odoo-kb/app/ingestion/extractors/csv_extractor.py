"""CSV content extractor."""
from __future__ import annotations

import csv
import io


class CsvExtractor:
    @property
    def supported_types(self) -> list[str]:
        return ["text/csv"]

    async def extract(self, content: bytes | str, content_type: str) -> str:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return ""

        # Treat first row as headers, format each subsequent row as key: value pairs
        headers = rows[0]
        parts = []
        for row in rows[1:]:
            pairs = []
            for i, cell in enumerate(row):
                header = headers[i] if i < len(headers) else f"column_{i}"
                if cell.strip():
                    pairs.append(f"{header}: {cell.strip()}")
            if pairs:
                parts.append("; ".join(pairs))

        return "\n".join(parts)
