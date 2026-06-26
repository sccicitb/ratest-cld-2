"""Cross-encoder reranking — bge-reranker-v2-m3, in-process via FlagEmbedding.

Flagged off by default (`settings.rerank_enabled = False`); recall-only is the
default retrieval path. The reranker (~2.3GB) is only ever loaded lazily, the
first time `rerank()` is actually called with rerank enabled — never at import
time, and never in tests by default (see `tests/test_retrieve.py`, which
monkeypatches the scoring function instead of loading real weights).
"""
from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.rag.vectors import Chunk


@lru_cache(maxsize=1)
def get_reranker():
    """Process-singleton: loads bge-reranker-v2-m3 once, lazily."""
    from FlagEmbedding import FlagReranker

    from app.rag.embedder import resolve_device, resolve_use_fp16

    device = resolve_device()
    use_fp16 = resolve_use_fp16(device)
    return FlagReranker(settings.rerank_model, use_fp16=use_fp16, devices=device)


def rerank(query: str, chunks: list[Chunk], k: int) -> list[Chunk]:
    """Re-score `chunks` against `query` with the cross-encoder; return top-k desc."""
    if not chunks:
        return []
    reranker = get_reranker()
    pairs = [[query, chunk["content"]] for chunk in chunks]
    scores = reranker.compute_score(pairs)
    if isinstance(scores, float):
        scores = [scores]
    ranked = sorted(zip(scores, chunks), key=lambda pair: pair[0], reverse=True)
    return [chunk for _, chunk in ranked[:k]]
