"""Retrieve tests: scoped search wrapper + fake-reranker ordering.

Embedder model load takes ~10-30s, so it's loaded once via a module-scoped
fixture (same pattern as tests/test_vectors.py). Rerank ordering is tested
with a monkeypatched fake reranker — the real bge-reranker-v2-m3 weights
(~2.3GB) are never loaded in tests.

M3 (Pillar 2 v1.1): retrieve() now requires caller_group_ids kwarg. KB access
is group/public-gated — not per-user. "cross_user" isolation test is reframed
as "caller outside all groups cannot see group-gated KB chunks".
"""
from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from app.rag import rerank as rerank_module
from app.rag.embedder import Embedder, get_embedder
from app.rag.retrieve import retrieve
from app.rag.vectors import ensure_collection, upsert_chunks


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return get_embedder()


@pytest.fixture()
def qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


_GROUP_A = "group-a"


def _make_chunks(user_id: str, session_id: str):
    """KB chunks belong to group-a (M3 group-gated). Session chunk is private."""
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


# --- Task 4.1 — retrieve() ---------------------------------------------------


def test_retrieve_returns_scoped_chunks(qdrant: QdrantClient, embedder: Embedder, monkeypatch):
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
    ensure_collection(qdrant)
    user_id, session_id = "user-1", "session-1"
    upsert_chunks(qdrant, embedder, _make_chunks(user_id, session_id))

    results = retrieve(
        query="quarterly revenue report",
        user_id=user_id,
        session_id=session_id,
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=embedder,
        k=5,
    )
    file_ids = {r["file_id"] for r in results}
    assert "file-session-1" in file_ids


def test_retrieve_recall_only_when_rerank_disabled(
    qdrant: QdrantClient, embedder: Embedder, monkeypatch
):
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
    called = False

    def _boom(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("rerank should not be called when disabled")

    monkeypatch.setattr("app.rag.retrieve.rerank", _boom)

    ensure_collection(qdrant)
    user_id, session_id = "user-1", "session-1"
    upsert_chunks(qdrant, embedder, _make_chunks(user_id, session_id))

    retrieve(
        query="cats domesticated mammals",
        user_id=user_id,
        session_id=session_id,
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=embedder,
        k=5,
    )
    assert called is False


def test_retrieve_cross_session_isolation(qdrant: QdrantClient, embedder: Embedder, monkeypatch):
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
    ensure_collection(qdrant)
    user_id, session_id = "user-1", "session-1"
    upsert_chunks(qdrant, embedder, _make_chunks(user_id, session_id))

    results = retrieve(
        query="quarterly revenue report",
        user_id=user_id,
        session_id="other-session",
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=embedder,
        k=5,
    )
    assert "file-session-1" not in {r["file_id"] for r in results}


def test_retrieve_caller_outside_group_sees_nothing(
    qdrant: QdrantClient, embedder: Embedder, monkeypatch
):
    """M3 group-gating: a caller in no group (caller_group_ids=[]) sees nothing.

    KB chunks belong to group-a (is_public=False). A caller with no groups
    cannot access them, and a mismatched user_id means no session chunks either.
    """
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
    ensure_collection(qdrant)
    user_id, session_id = "user-1", "session-1"
    upsert_chunks(qdrant, embedder, _make_chunks(user_id, session_id))

    results = retrieve(
        query="quarterly revenue report cats",
        user_id="other-user",
        session_id=session_id,
        caller_group_ids=[],  # no group membership → KB chunks invisible
        client=qdrant,
        embedder=embedder,
        k=5,
    )
    assert results == []


def test_retrieve_uses_wider_recall_when_rerank_enabled(
    qdrant: QdrantClient, embedder: Embedder, monkeypatch
):
    """With rerank enabled, retrieve fetches a wider recall set then reranks."""
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", True)

    captured_k = {}

    def _fake_search(
        client, embedder, *, query, user_id, session_id, caller_group_ids, k=5, tags=None
    ):
        captured_k["k"] = k
        return [
            {
                "content": "alpha",
                "file_id": "f1",
                "chunk_idx": 0,
                "tags": [],
                "user_id": user_id,
                "scope": "kb",
                "session_id": None,
                "status": "ready",
                "group_id": None,
                "is_public": True,
            }
        ]

    def _fake_rerank(query, chunks, k):
        return chunks[:k]

    monkeypatch.setattr("app.rag.retrieve.search", _fake_search)
    monkeypatch.setattr("app.rag.retrieve.rerank", _fake_rerank)

    out = retrieve(
        query="x", user_id="u", session_id=None, caller_group_ids=[], client=qdrant,
        embedder=embedder, k=3,
    )
    assert captured_k["k"] == 30  # k * multiplier
    assert len(out) == 1


def test_retrieve_with_tags_filters_to_matching_chunks(
    qdrant: QdrantClient, embedder: Embedder, monkeypatch
):
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
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

    tagged = retrieve(
        query="team process margins",
        user_id=user_id,
        session_id=None,
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=embedder,
        k=5,
        tags=["finance"],
    )
    assert {r["file_id"] for r in tagged} == {"file-finance"}

    untagged = retrieve(
        query="team process margins",
        user_id=user_id,
        session_id=None,
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=embedder,
        k=5,
        tags=None,
    )
    assert {r["file_id"] for r in untagged} == {"file-finance", "file-hr"}


# --- Task 4.4 — rerank ordering (fake reranker, no real weights loaded) -----


class _FakeReranker:
    def __init__(self, score_map: dict[str, float]):
        self._score_map = score_map

    def compute_score(self, pairs):
        return [self._score_map[passage] for _, passage in pairs]


def test_rerank_reorders_by_fake_score_desc_and_truncates(monkeypatch):
    chunks = [
        {
            "content": f"chunk-{i}",
            "file_id": f"f{i}",
            "chunk_idx": 0,
            "tags": [],
            "user_id": "u",
            "scope": "kb",
            "session_id": None,
            "status": "ready",
            "group_id": None,
            "is_public": True,
        }
        for i in range(3)
    ]
    # chunk-0 -> low, chunk-1 -> high, chunk-2 -> mid
    fake = _FakeReranker({"chunk-0": 0.1, "chunk-1": 0.9, "chunk-2": 0.5})
    monkeypatch.setattr(rerank_module, "get_reranker", lambda: fake)

    out = rerank_module.rerank("irrelevant query", chunks, k=2)
    assert [c["file_id"] for c in out] == ["f1", "f2"]


def test_rerank_empty_chunks_returns_empty(monkeypatch):
    def _boom():
        raise AssertionError("should not load reranker for empty input")

    monkeypatch.setattr(rerank_module, "get_reranker", _boom)
    assert rerank_module.rerank("q", [], k=5) == []
