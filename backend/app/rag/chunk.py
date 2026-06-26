"""Structure-aware chunking (§8.1): paragraphs/sentences packed to ~target size.

Tokens are approximated by whitespace word count — no tokenizer dependency,
good enough for sizing chunks that get embedded by BGE-M3 downstream. Chunks
are packed greedily from paragraphs (falling back to sentence splits for
paragraphs that alone exceed the target), and consecutive chunks carry an
`overlap` fraction of trailing words into the next chunk's start so retrieval
doesn't lose context at a chunk boundary.
"""
from __future__ import annotations

import re

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]


def _split_sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]


def _words(s: str) -> list[str]:
    return s.split()


def chunk(text: str, target_tokens: int = 800, overlap: float = 0.12) -> list[str]:
    """Split `text` into chunks of ~target_tokens words with carried overlap."""
    if not text or not text.strip():
        return []

    # Flatten into a single ordered list of sentence-level units, splitting
    # any paragraph that alone is larger than the target into sentences.
    units: list[str] = []
    for paragraph in _split_paragraphs(text):
        if len(_words(paragraph)) <= target_tokens:
            units.append(paragraph)
        else:
            units.extend(_split_sentences(paragraph))

    if not units:
        return []

    overlap_words = max(0, int(target_tokens * overlap))

    chunks: list[str] = []
    current_words: list[str] = []

    for unit in units:
        unit_words = _words(unit)

        if current_words and len(current_words) + len(unit_words) > target_tokens:
            chunks.append(" ".join(current_words))
            # Carry the trailing `overlap_words` words into the next chunk.
            current_words = current_words[-overlap_words:] if overlap_words else []

        current_words.extend(unit_words)

    if current_words:
        chunks.append(" ".join(current_words))

    return chunks
