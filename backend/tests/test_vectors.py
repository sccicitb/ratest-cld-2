"""Vector infra tests: BGE-M3 embedder + Qdrant gateway (scoped hybrid search).

Embedder model load takes ~10-30s, so it's loaded once via a module-scoped
fixture for the whole file.

M3 (Pillar 2 v1.1): KB access is now group/public-gated, not user-gated.
  - KB chunks need group_id + is_public fields.
  - search() requires caller_group_ids kwarg.
  - "different user can't see KB" → reframed as "caller not in the group
    (caller_group_ids=[]) can't see group-gated KB chunks".
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

_GROUP_A = "group-a"


def _make_chunks(user_id: str, session_id: str):
    """Chunks for round-trip tests. KB chunks belong to group-a (M3)."""
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
            "group_id": _GROUP_A,
            "is_public": False,
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
            "group_id": _GROUP_A,
            "is_public": False,
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
            "group_id": None,
            "is_public": False,
        },
    ]


def test_upsert_and_scoped_search_round_trip(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    results = search(
        qdrant, embedder, query="quarterly revenue report", user_id=user_id,
        session_id=session_id, caller_group_ids=[_GROUP_A], k=5,
    )
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

    results = search(
        qdrant, embedder, query="quarterly revenue report", user_id=user_id,
        session_id="other-session", caller_group_ids=[_GROUP_A], k=5,
    )
    file_ids = {r["file_id"] for r in results}
    assert "file-session-1" not in file_ids


def test_search_returns_nothing_for_caller_outside_group(
    qdrant: QdrantClient, embedder: Embedder
):
    """M3 group-gating: a caller in no groups cannot see group-gated KB chunks.

    The KB chunks belong to group-a (is_public=False). A caller with
    caller_group_ids=[] has no group membership, so neither branch of the
    scope filter matches — results must be empty.
    """
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    # Caller has no group membership AND is a different user (no session ownership)
    results = search(
        qdrant, embedder, query="quarterly revenue report cats dogs",
        user_id="other-user", session_id="other-session", caller_group_ids=[], k=5,
    )
    assert results == []


def test_search_with_session_id_none_returns_kb_chunks_only(
    qdrant: QdrantClient, embedder: Embedder
):
    """When session_id=None, KB chunks are returned but session chunks are excluded."""
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    # Query with session_id=None; should get KB chunks but NOT session chunk
    results = search(
        qdrant, embedder, query="cats domesticated mammals",
        user_id=user_id, session_id=None, caller_group_ids=[_GROUP_A], k=5,
    )
    file_ids = {r["file_id"] for r in results}

    # KB chunks should be returned (caller is in group-a)
    assert "file-kb-1" in file_ids or "file-kb-2" in file_ids
    # Session chunk must NOT be returned (the security fix: no empty-string match leak)
    assert "file-session-1" not in file_ids


# --- Task 2.4 — Delete + payload update -------------------------------------


def test_delete_by_file_removes_points(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    delete_by_file(qdrant, "file-kb-1")

    results = search(
        qdrant, embedder, query="cats domesticated mammals", user_id=user_id,
        session_id=session_id, caller_group_ids=[_GROUP_A], k=5,
    )
    file_ids = {r["file_id"] for r in results}
    assert "file-kb-1" not in file_ids


def test_delete_by_session_removes_points(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    delete_by_session(qdrant, session_id)

    results = search(
        qdrant, embedder, query="quarterly revenue report", user_id=user_id,
        session_id=session_id, caller_group_ids=[_GROUP_A], k=5,
    )
    file_ids = {r["file_id"] for r in results}
    assert "file-session-1" not in file_ids


def test_search_with_tags_filters_to_matching_chunks(qdrant: QdrantClient, embedder: Embedder):
    ensure_collection(qdrant)
    user_id = "user-1"
    chunks = [
        {
            "content": "The finance team closed Q1 with strong margins.",
            "file_id": "file-finance",
            "chunk_idx": 0,
            "tags": ["finance"],
            "user_id": user_id,
            "scope": "kb",
            "session_id": None,
            "status": "ready",
            "group_id": _GROUP_A,
            "is_public": False,
        },
        {
            "content": "The HR team rolled out a new onboarding process.",
            "file_id": "file-hr",
            "chunk_idx": 0,
            "tags": ["hr"],
            "user_id": user_id,
            "scope": "kb",
            "session_id": None,
            "status": "ready",
            "group_id": _GROUP_A,
            "is_public": False,
        },
    ]
    upsert_chunks(qdrant, embedder, chunks)

    tagged = search(
        qdrant, embedder, query="team process margins", user_id=user_id,
        session_id=None, caller_group_ids=[_GROUP_A], k=5, tags=["finance"],
    )
    assert {r["file_id"] for r in tagged} == {"file-finance"}

    untagged = search(
        qdrant, embedder, query="team process margins", user_id=user_id,
        session_id=None, caller_group_ids=[_GROUP_A], k=5, tags=None,
    )
    assert {r["file_id"] for r in untagged} == {"file-finance", "file-hr"}


def test_update_file_payload_promotes_session_chunk_to_kb(
    qdrant: QdrantClient, embedder: Embedder
):
    ensure_collection(qdrant)
    user_id = "user-1"
    session_id = "session-1"
    chunks = _make_chunks(user_id, session_id)
    upsert_chunks(qdrant, embedder, chunks)

    # Before promotion: a different session (or no session) shouldn't see file-session-1.
    results = search(
        qdrant, embedder, query="quarterly revenue report", user_id=user_id,
        session_id="other-session", caller_group_ids=[_GROUP_A], k=5,
    )
    assert "file-session-1" not in {r["file_id"] for r in results}

    # Promote to public KB doc (set is_public=True so any caller can see it).
    update_file_payload(
        qdrant, "file-session-1",
        {"scope": "kb", "session_id": None, "is_public": True},
    )

    # After promotion: visible regardless of session_id or group membership.
    results = search(
        qdrant, embedder, query="quarterly revenue report", user_id=user_id,
        session_id="other-session", caller_group_ids=[], k=5,
    )
    assert "file-session-1" in {r["file_id"] for r in results}
