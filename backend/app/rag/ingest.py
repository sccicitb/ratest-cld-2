"""Ingest orchestration (§8.1, §8.3): extract → chunk → embed → upsert → status.

`ingest()` is an async generator yielding progress dicts shaped for SSE
(`{"type": "chunk_progress", ...}`) as it embeds/upserts chunks in batches.
The Qdrant client and embedder are injected (not the process singletons)
so callers/tests can swap in an in-memory client / fake embedder.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session

from app.rag.chunk import chunk as chunk_text
from app.rag.embedder import Embedder
from app.rag.extract import extract_text
from app.rag.vectors import Chunk, ensure_collection, upsert_chunks
from app.storage import open_blob

_EMBED_BATCH = 16


async def ingest(
    db: Session, file_id: str, *, client: QdrantClient, embedder: Embedder
) -> AsyncIterator[dict[str, Any]]:
    """Extract, chunk, embed, and upsert a `KBFile`'s blob; finalize its status.

    Yields `chunk_progress` events as batches are embedded/upserted. On any
    failure (extraction, chunking, embed, or upsert), marks the file
    `status="error"` and re-raises so the caller (route) can surface an error event.
    """
    from app.models import KBFile  # local import: avoid import cycles

    file = db.get(KBFile, file_id)
    if file is None:
        raise ValueError(f"KBFile not found: {file_id!r}")

    # Open the blob just to confirm it's readable; extract_text re-opens it
    # internally via storage_key (keeps extract.py the single point of truth
    # for the on-disk layout).
    with open_blob(file.storage_key):
        pass

    try:
        text = extract_text(file.storage_key, file.name)
        pieces = chunk_text(text)

        total = len(pieces)

        if total == 0:
            file.status = "ready"
            file.chunk_count = 0
            db.commit()
            yield {
                "type": "chunk_progress",
                "fileName": file.name,
                "progress": 100,
                "chunkCount": 0,
                "total": 0,
            }
            return

        chunks: list[Chunk] = [
            {
                "content": piece,
                "file_id": file.id,
                "chunk_idx": idx,
                "tags": list(file.tags or []),
                "user_id": file.user_id,
                "scope": file.scope,
                "session_id": file.session_id,
                "status": "ready",
            }
            for idx, piece in enumerate(pieces)
        ]

        ensure_collection(client)

        done = 0
        for start in range(0, total, _EMBED_BATCH):
            batch = chunks[start : start + _EMBED_BATCH]
            upsert_chunks(client, embedder, batch)
            done += len(batch)
            yield {
                "type": "chunk_progress",
                "fileName": file.name,
                "progress": round(done / total * 100),
                "chunkCount": done,
                "total": total,
            }

        file.status = "ready"
        file.chunk_count = total
        db.commit()
    except Exception:
        file.status = "error"
        file.chunk_count = 0
        db.commit()
        raise
