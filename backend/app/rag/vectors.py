"""Qdrant gateway — collection bootstrap, scoped hybrid search, mutate (§9.2).

Embeddings are pinned to BGE-M3: dense 1024-dim (Cosine) + sparse, named
vectors `dense` + `sparse` on a single collection `kb_chunks`. Every point
denormalizes the scope payload (`user_id`, `scope`, `session_id`, `status`,
...) so the security boundary lives in a single filter applied at search
time — see `_scope_filter`.
"""

from __future__ import annotations

import uuid
from typing import TypedDict

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.config import settings
from app.rag.embedder import Embedder

COLLECTION = settings.qdrant_collection or "kb_chunks"

_PAYLOAD_INDEX_FIELDS = ("user_id", "scope", "session_id", "status")


class Chunk(TypedDict):
    content: str
    file_id: str
    chunk_idx: int
    tags: list[str]
    user_id: str
    scope: str
    session_id: str | None
    status: str


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


def _scope_filter(*, user_id: str, session_id: str | None) -> qm.Filter:
    """The security boundary: owner + ready + (kb OR matching-session).

    When session_id is None (pure KB query), the filter only permits scope=kb.
    When session_id is provided, the filter permits scope=kb OR (scope=session AND matching session_id).
    """
    should = [qm.FieldCondition(key="scope", match=qm.MatchValue(value="kb"))]
    if session_id is not None:
        should.append(
            qm.Filter(
                must=[
                    qm.FieldCondition(key="scope", match=qm.MatchValue(value="session")),
                    qm.FieldCondition(key="session_id", match=qm.MatchValue(value=session_id)),
                ]
            )
        )
    return qm.Filter(
        must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id)),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="ready")),
            qm.Filter(should=should),
        ]
    )


def search(
    client: QdrantClient,
    embedder: Embedder,
    *,
    query: str,
    user_id: str,
    session_id: str | None,
    k: int = 5,
    tags: list[str] | None = None,
) -> list[Chunk]:
    emb = embedder.embed_query(query)
    scope_filter = _scope_filter(user_id=user_id, session_id=session_id)
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
