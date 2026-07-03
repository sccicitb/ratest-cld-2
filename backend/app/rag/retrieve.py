"""Read-side retrieval — scoped hybrid search, optionally reranked (§7, §8.5).

`retrieve()` is the single entry point tools call. It never trusts
model-supplied scope: callers (the tool layer) must pass `user_id`/`session_id`
from server-side context, not from model `args`. `caller_group_ids` is
resolved from the authenticated user's group membership (M3).
"""
from __future__ import annotations

import hashlib

from qdrant_client import QdrantClient

from app.config import settings
from app.rag.embedder import Embedder
from app.rag.rerank import rerank
from app.rag.vectors import Chunk, search

_RERANK_RECALL_MULTIPLIER = 10
_RERANK_RECALL_CAP = 50


def _dedup(chunks: list[Chunk]) -> list[Chunk]:
    """Drop chunks with identical content; keep first occurrence (highest-scored).

    Works across files and groups: if the same text appears in multiple docs
    that a user can access (e.g. a doc copied into two groups, user in both),
    retrieval returns the content once.
    """
    seen: set[str] = set()
    out: list[Chunk] = []
    for chunk in chunks:
        h = hashlib.sha256(chunk["content"].encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(chunk)
    return out


def retrieve(
    *,
    query: str,
    user_id: str,
    session_id: str | None,
    caller_group_ids: list[str],
    client: QdrantClient,
    embedder: Embedder,
    k: int = 5,
    tags: list[str] | None = None,
) -> list[Chunk]:
    """Scoped hybrid search with content-hash dedup; reranked if `settings.rerank_enabled`."""
    if not settings.rerank_enabled:
        chunks = search(
            client,
            embedder,
            query=query,
            user_id=user_id,
            session_id=session_id,
            caller_group_ids=caller_group_ids,
            k=k,
            tags=tags,
        )
        return _dedup(chunks)

    recall_k = min(k * _RERANK_RECALL_MULTIPLIER, _RERANK_RECALL_CAP)
    candidates = search(
        client,
        embedder,
        query=query,
        user_id=user_id,
        session_id=session_id,
        caller_group_ids=caller_group_ids,
        k=recall_k,
        tags=tags,
    )
    return _dedup(rerank(query, candidates, k))
