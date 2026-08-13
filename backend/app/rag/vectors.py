"""Qdrant gateway — collection bootstrap, scoped hybrid search, mutate (§9.2).

Embeddings are pinned to BGE-M3: dense 1024-dim (Cosine) + sparse, named
vectors `dense` + `sparse` on a single collection `kb_chunks`. Every point
denormalizes the scope payload (`user_id`, `scope`, `session_id`, `status`,
`group_id`, `is_public`, ...) so the security boundary lives in a single
filter applied at search time — see `_scope_filter`.

M3 access rule (§8):
  status=="ready" AND (
    (scope=="kb"      AND (is_public OR group_id ∈ caller_group_ids))
    OR (scope=="session" AND session_id==current AND user_id==caller)
  )
"""

from __future__ import annotations

import uuid
from typing import TypedDict

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.config import settings
from app.rag.embedder import Embedder

COLLECTION = settings.qdrant_collection or "kb_chunks"

# `group_id` is indexed (KEYWORD); `is_public` (bool) is left unindexed —
# Qdrant handles boolean payload filtering without a dedicated index.
_PAYLOAD_INDEX_FIELDS = ("user_id", "scope", "session_id", "status", "group_id")


class Chunk(TypedDict):
    content: str
    file_id: str
    chunk_idx: int
    tags: list[str]
    user_id: str
    scope: str
    session_id: str | None
    status: str
    # M3 additions
    group_id: str | None
    is_public: bool


def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(client: QdrantClient) -> None:
    """Create `kb_chunks` with named dense+sparse vectors, idempotently."""
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            COLLECTION,
            vectors_config={"dense": qm.VectorParams(size=1024, distance=qm.Distance.COSINE)},
            sparse_vectors_config={"sparse": qm.SparseVectorParams()},
        )
        for field in _PAYLOAD_INDEX_FIELDS:
            client.create_payload_index(
                COLLECTION,
                field_name=field,
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )


def _point_id(file_id: str, chunk_idx: int) -> str:
    """Deterministic UUID5 so re-upserting the same chunk overwrites it."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_id}:{chunk_idx}"))


def upsert_chunks(client: QdrantClient, embedder: Embedder, chunks: list[Chunk]) -> None:
    if not chunks:
        return
    embeddings = embedder.embed_passages([c["content"] for c in chunks])
    points = []
    for chunk, emb in zip(chunks, embeddings):
        points.append(
            qm.PointStruct(
                id=_point_id(chunk["file_id"], chunk["chunk_idx"]),
                vector={
                    "dense": emb["dense"],
                    "sparse": qm.SparseVector(
                        indices=emb["sparse"]["indices"],
                        values=emb["sparse"]["values"],
                    ),
                },
                payload=dict(chunk),
            )
        )
    client.upsert(COLLECTION, points=points)


def _scope_filter(
    *,
    user_id: str,
    session_id: str | None,
    caller_group_ids: list[str],
) -> qm.Filter:
    """The M3 security boundary.

    KB branch — requires scope=kb AND (is_public=True OR group_id ∈ caller_group_ids).
    Session branch — requires scope=session AND matching session_id AND user_id.
    A no-group caller (caller_group_ids=[]) can only see public KB docs +
    their own session files.
    """
    # KB branch: is_public OR group_id in caller's groups
    kb_should: list[qm.Condition] = [
        qm.FieldCondition(key="is_public", match=qm.MatchValue(value=True))
    ]
    if caller_group_ids:
        kb_should.append(
            qm.FieldCondition(key="group_id", match=qm.MatchAny(any=caller_group_ids))
        )
    kb_branch = qm.Filter(
        must=[
            qm.FieldCondition(key="scope", match=qm.MatchValue(value="kb")),
            qm.Filter(should=kb_should),
        ]
    )

    top_should: list[qm.Condition] = [kb_branch]

    if session_id is not None:
        session_branch = qm.Filter(
            must=[
                qm.FieldCondition(key="scope", match=qm.MatchValue(value="session")),
                qm.FieldCondition(key="session_id", match=qm.MatchValue(value=session_id)),
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id)),
            ]
        )
        top_should.append(session_branch)

    return qm.Filter(
        must=[
            qm.FieldCondition(key="status", match=qm.MatchValue(value="ready")),
            qm.Filter(should=top_should),
        ]
    )


def search(
    client: QdrantClient,
    embedder: Embedder,
    *,
    query: str,
    user_id: str,
    session_id: str | None,
    caller_group_ids: list[str],
    k: int = 5,
    tags: list[str] | None = None,
) -> list[Chunk]:
    # Nothing ingested yet is an empty KB, not a failure. `ensure_collection`
    # runs on ingest, so a fresh deploy — or the collection delete in
    # DEPLOY.md §7 — leaves no collection for `query_points` to read, and it
    # raises ValueError rather than returning nothing. Guarded the same way
    # `delete_by_session` already is.
    if not client.collection_exists(COLLECTION):
        return []
    emb = embedder.embed_query(query)
    scope_filter = _scope_filter(
        user_id=user_id, session_id=session_id, caller_group_ids=caller_group_ids
    )
    if tags:
        scope_filter.must.append(qm.FieldCondition(key="tags", match=qm.MatchAny(any=tags)))
    res = client.query_points(
        COLLECTION,
        prefetch=[
            qm.Prefetch(query=emb["dense"], using="dense", limit=50, filter=scope_filter),
            qm.Prefetch(
                query=qm.SparseVector(**emb["sparse"]),
                using="sparse",
                limit=50,
                filter=scope_filter,
            ),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        limit=k,
        with_payload=True,
    )
    return [p.payload for p in res.points]


def delete_by_file(client: QdrantClient, file_id: str) -> None:
    client.delete(
        COLLECTION,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="file_id", match=qm.MatchValue(value=file_id))]
            )
        ),
    )


def delete_by_session(client: QdrantClient, session_id: str) -> None:
    """Remove all points belonging to `session_id`. Safe to call even when the
    collection hasn't been created yet (no ingested files)."""
    if not client.collection_exists(COLLECTION):
        return
    client.delete(
        COLLECTION,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="session_id", match=qm.MatchValue(value=session_id))]
            )
        ),
    )


def update_file_payload(client: QdrantClient, file_id: str, patch: dict) -> None:
    """Promote/reindex: patch the denormalized payload for all of a file's points."""
    client.set_payload(
        COLLECTION,
        payload=patch,
        points=qm.Filter(
            must=[qm.FieldCondition(key="file_id", match=qm.MatchValue(value=file_id))]
        ),
    )
