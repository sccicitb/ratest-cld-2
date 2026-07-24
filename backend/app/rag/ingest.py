"""Ingest orchestration (§8.1, §8.3): extract → chunk → embed → upsert → status.

`ingest()` is an async generator yielding progress dicts shaped for SSE
(`{"type": "chunk_progress", ...}`) as it embeds/upserts chunks in batches.
The Qdrant client and embedder are injected (not the process singletons)
so callers/tests can swap in an in-memory client / fake embedder.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session

from app.rag.chunk import chunk as chunk_text
from app.rag.embedder import Embedder
from app.rag.extract import extract_text
from app.rag.vectors import Chunk, ensure_collection, upsert_chunks
from app.storage import open_blob

_EMBED_BATCH = 16

# Single worker: serializes the CPU/GPU-bound ingest work (PDFOxide/PaddleOCR
# extraction, BGE-M3 embedding) — correct on one box, and sidesteps the unproven
# thread-safety of the shared OCR engine / embedder under concurrent calls —
# while keeping it OFF the event loop so a long ingest doesn't stall the worker.
# Process-lived; Python's atexit reaps it on shutdown.
_CPU_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest-cpu")


async def ingest(
    db: Session, file_id: str, *, client: QdrantClient, embedder: Embedder
) -> AsyncIterator[dict[str, Any]]:
    """Extract, chunk, embed, and upsert a `KBFile`'s blob; finalize its status.

    Yields `chunk_progress` events as batches are embedded/upserted. If the
    ingest doesn't reach a terminal `status="ready"` — whether from a normal
    failure (extraction, chunking, embed, upsert) or a mid-stream cancellation
    (a client disconnect raises asyncio.CancelledError, a BaseException) — the
    file is finalized to `status="error"` so it's never stranded at "indexing"
    (visible in the UI but with no/partial chunks, so never retrievable). Real
    failures still propagate so the caller (route) can surface an error event.
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

    loop = asyncio.get_running_loop()
    finalized = False
    try:
        text = await loop.run_in_executor(
            _CPU_EXECUTOR, extract_text, file.storage_key, file.name
        )
        pieces = chunk_text(text)

        total = len(pieces)

        if total == 0:
            file.status = "ready"
            file.chunk_count = 0
            db.commit()
            finalized = True
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
                # M3: denormalize group gating onto each chunk
                "group_id": file.group_id,
                "is_public": file.is_public,
            }
            for idx, piece in enumerate(pieces)
        ]

        ensure_collection(client)

        done = 0
        for start in range(0, total, _EMBED_BATCH):
            batch = chunks[start : start + _EMBED_BATCH]
            await loop.run_in_executor(_CPU_EXECUTOR, upsert_chunks, client, embedder, batch)
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
        finalized = True
    finally:
        if not finalized:
            # Didn't reach a terminal 'ready'. Covers both a normal Exception
            # (extract/chunk/embed/upsert failure — which still propagates out
            # of the `finally`) AND a BaseException like asyncio.CancelledError
            # raised when the client disconnects mid-stream (refresh / network
            # loss); the latter would slip past an `except Exception` and strand
            # the file at 'indexing'. Finalize to 'error' so the state is honest
            # and the file can be reindexed.
            file.status = "error"
            file.chunk_count = 0
            db.commit()
