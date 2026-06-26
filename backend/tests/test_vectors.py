"""Vector infra tests: BGE-M3 embedder + Qdrant gateway (scoped hybrid search).

Embedder model load takes ~10-30s, so it's loaded once via a module-scoped
fixture for the whole file.
"""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from app.rag.embedder import Embedder, get_embedder
from app.rag.vectors import (
    COLLECTION,
    delete_by_file,
    delete_by_session,
    ensure_collection,
    search,
    update_file_payload,
    upsert_chunks,
)


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return get_embedder()


@pytest.fixture()
def qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


# --- Task 2.1 — Embedder ---------------------------------------------------


def test_embed_query_returns_dense_1024_and_nonempty_sparse(embedder: Embedder):
    emb = embedder.embed_query("hello world")
    assert len(emb["dense"]) == 1024
    assert all(isinstance(x, float) for x in emb["dense"])
    assert len(emb["sparse"]["indices"]) > 0
    assert len(emb["sparse"]["indices"]) == len(emb["sparse"]["values"])


def test_embed_passages_batches(embedder: Embedder):
    out = embedder.embed_passages(["first passage", "second passage", "third one"])
    assert len(out) == 3
    for emb in out:
        assert len(emb["dense"]) == 1024
        assert len(emb["sparse"]["indices"]) > 0


def test_get_embedder_is_singleton():
    assert get_embedder() is get_embedder()


# --- Task 2.2 — Collection bootstrap ---------------------------------------


def test_ensure_collection_is_idempotent_and_has_named_vectors(qdrant: QdrantClient):
    ensure_collection(qdrant)
    ensure_collection(qdrant)  # must not raise on second call

    info = qdrant.get_collection(COLLECTION)
    assert "dense" in info.config.params.vectors
    assert info.config.params.vectors["dense"].size == 1024
    assert "sparse" in info.config.params.sparse_vectors


# --- Task 2.3 — Upsert + scoped search round-trip ---------------------------


def _make_chunks(user_id: str, session_id: str):
    return [
        {
            "content": "Cats are small domesticated carnivorous mammals.",
            "file_id": "file-kb-1",
            "chunk_idx": 0,
            "tags": ["animals"],
            "user_id": user_id,
            "scope": "kb",
            "session_id": None,
            "status": "ready",
        },
        {
            "content": "Dogs are loyal companions and have been domesticated for millennia.",
            "file_id": "file-kb-2",
            "chunk_idx": 0,
            "tags": ["animals"],
            "user_id": user_id,
            "scope": "kb",
            "session_id": None,
            "status": "ready",
        },
        {
            "content": "The quarterly report shows a 12% increase in revenue this session.",
            "file_id": "file-session-1",
            "chunk_idx": 0,
            "tags": ["finance"],
            "user_id": user_id,
            "scope": "session",
            "session_id": session_id,
            "status": "ready",
        },
    ]


def test_upsert_and_scoped_search_round_trip(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    results = search(qdrant, embedder, query="quarterly revenue report", user_id=user_id,
                      session_id=session_id, k=5)
    file_ids = {r["file_id"] for r in results}
    assert "file-session-1" in file_ids
    assert "file-kb-1" in file_ids or "file-kb-2" in file_ids


def test_search_excludes_session_chunk_for_different_session(
    qdrant: QdrantClient, embedder: Embedder
):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    results = search(qdrant, embedder, query="quarterly revenue report", user_id=user_id,
                      session_id="other-session", k=5)
    file_ids = {r["file_id"] for r in results}
    assert "file-session-1" not in file_ids


def test_search_returns_nothing_for_different_user(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    results = search(qdrant, embedder, query="quarterly revenue report cats dogs",
                      user_id="other-user", session_id=session_id, k=5)
    assert results == []


# --- Task 2.4 — Delete + payload update -------------------------------------


def test_delete_by_file_removes_points(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    delete_by_file(qdrant, "file-kb-1")

    results = search(qdrant, embedder, query="cats domesticated mammals", user_id=user_id,
                      session_id=session_id, k=5)
    file_ids = {r["file_id"] for r in results}
    assert "file-kb-1" not in file_ids


def test_delete_by_session_removes_points(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    delete_by_session(qdrant, session_id)

    results = search(qdrant, embedder, query="quarterly revenue report", user_id=user_id,
                      session_id=session_id, k=5)
    file_ids = {r["file_id"] for r in results}
    assert "file-session-1" not in file_ids


def test_update_file_payload_promotes_session_chunk_to_kb(
    qdrant: QdrantClient, embedder: Embedder
):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    # Before promotion: a different session shouldn't see file-session-1.
    results = search(qdrant, embedder, query="quarterly revenue report", user_id=user_id,
                      session_id="other-session", k=5)
    assert "file-session-1" not in {r["file_id"] for r in results}

    update_file_payload(qdrant, "file-session-1", {"scope": "kb", "session_id": None})

    # After promotion: now visible regardless of session_id (scope=kb).
    results = search(qdrant, embedder, query="quarterly revenue report", user_id=user_id,
                      session_id="other-session", k=5)
    assert "file-session-1" in {r["file_id"] for r in results}
