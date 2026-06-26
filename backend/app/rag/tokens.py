"""Token-count routing — decide whether a file flies inline or gets ingested.

Approximates tokens by whitespace-word count (same approach as chunk.py),
keeping the backend free of a tokenizer dependency. The threshold is
`settings.inline_token_budget` (§6).
"""

from __future__ import annotations

from typing import Literal

from app.config import settings


def route_by_tokens(text: str) -> Literal["inline", "ingest"]:
    """Return the routing decision for a document's extracted text.

    * Empty / whitespace-only -> ``"inline"`` (trivial).
    * word count <= ``settings.inline_token_budget`` -> ``"inline"``.
    * Otherwise -> ``"ingest"``.
    """
    if not text or not text.strip():
        return "inline"
    word_count = len(text.split())
    return "inline" if word_count <= settings.inline_token_budget else "ingest"
