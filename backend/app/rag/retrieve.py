"""Read-side retrieval — scoped hybrid search, optionally reranked (§7, §8.5).

`retrieve()` is the single entry point tools call. It never trusts
model-supplied scope: callers (the tool layer) must pass `user_id`/`session_id`
from server-side context, not from model `args`.
"""
from __future__ import annotations

from qdrant_client import QdrantClient

from app.config import settings
from app.rag.embedder import Embedder
from app.rag.rerank import rerank
from app.rag.vectors import Chunk, search

_RERANK_RECALL_MULTIPLIER = 10
_RERANK_RECALL_CAP = 50


def retrieve(
    *,
    query: str,
    user_id: str,
    session_id: str | None,
    client: QdrantClient,
    embedder: Embedder,
    k: int = 5,
) -> list[Chunk]:
    """Scoped hybrid search; reranked on top if `settings.rerank_enabled`."""
    if not settings.rerank_enabled:
        return search(client, embedder, query=query, user_id=user_id, session_id=session_id, k=k)

    recall_k = min(k * _RERANK_RECALL_MULTIPLIER, _RERANK_RECALL_CAP)
    candidates = search(
        client, embedder, query=query, user_id=user_id, session_id=session_id, k=recall_k
    )
    return rerank(query, candidates, k)
