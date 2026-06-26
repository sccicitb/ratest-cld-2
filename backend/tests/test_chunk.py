"""Tests for app.rag.chunk (Task 3.3): structure-aware packing with overlap."""
from __future__ import annotations

from app.rag.chunk import chunk


def test_chunk_empty_text_returns_empty_list():
    assert chunk("") == []
    assert chunk("   \n\n  ") == []


def test_chunk_short_text_returns_single_chunk():
    text = "This is a short paragraph that fits in one chunk easily."
    out = chunk(text, target_tokens=800)
    assert len(out) == 1
    assert out[0].strip() == text


def test_chunk_long_text_returns_multiple_chunks_each_within_target():
    # ~5000 chars of repeated paragraphs, well beyond target_tokens=800 words.
    paragraph = "word " * 50  # 50 words per paragraph
    text = "\n\n".join([paragraph] * 40)  # ~2000 words total
    out = chunk(text, target_tokens=100, overlap=0.12)

    assert len(out) > 1
    for c in out:
        word_count = len(c.split())
        # Allow slack for overlap carried into the chunk.
        assert word_count <= 100 * 1.5


def test_chunk_consecutive_chunks_overlap():
    text = "\n\n".join(
        f"sentence number {i} with some extra padding words here." for i in range(200)
    )
    out = chunk(text, target_tokens=50, overlap=0.2)

    assert len(out) > 1
    # Some suffix words of chunk[i] should reappear as prefix words of chunk[i+1].
    for i in range(len(out) - 1):
        tail_words = out[i].split()[-3:]
        head_words = out[i + 1].split()[: len(out[i].split())]
        overlap_found = any(w in head_words for w in tail_words)
        assert overlap_found, f"No overlap detected between chunk {i} and {i + 1}"
