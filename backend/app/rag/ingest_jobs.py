"""Detached, app-owned ingest tasks + a startup reaper.

KB ingest runs inside these tasks (not inside the request's SSE response), so a
client disconnect cancels only the observing response — the task keeps running
to completion. The registry (a process singleton on app.state) holds the task
reference that keeps it alive after the request ends.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.rag.ingest import ingest

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE = 64
_TERMINAL = object()  # sentinel: no more progress events for this job


def _offer(queue: "asyncio.Queue", item: object) -> None:
    """put_nowait, dropping the oldest on overflow. Progress is monotonic, so a
    coalesced/jumped bar is fine, and a vanished observer can't make us leak."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass


class IngestJob:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self.task: asyncio.Task | None = None


class IngestJobRegistry:
    """Owns detached ingest tasks. Built once at startup with the app singletons."""

    def __init__(self, *, session_factory, client, embedder, max_concurrent: int) -> None:
        self._jobs: dict[str, IngestJob] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._session_factory = session_factory
        self._client = client
        self._embedder = embedder

    def spawn(self, file_id: str) -> IngestJob:
        job = IngestJob(file_id)
        job.task = asyncio.create_task(self._run(job))
        self._jobs[file_id] = job
        # The dict entry is the GC anchor; evict when the task finishes, but only
        # if the entry still points at THIS job. A later spawn(file_id) for the
        # same file replaces the entry with a new job; this job's completion
        # must not evict that still-running replacement.
        job.task.add_done_callback(
            lambda _t, fid=file_id, this=job: (
                self._jobs.pop(fid, None) if self._jobs.get(fid) is this else None
            )
        )
        return job

    async def _run(self, job: IngestJob) -> None:
        try:
            async with self._sem:
                db = self._session_factory()
                try:
                    async for ev in ingest(
                        db, job.file_id, client=self._client, embedder=self._embedder
                    ):
                        _offer(job.queue, ev)
                finally:
                    db.close()
        except Exception:
            # ingest already marked the file 'error' via its own finally; log and
            # still terminate the stream so the observer/route can resolve.
            logger.exception("ingest task failed for %s", job.file_id)
        finally:
            _offer(job.queue, _TERMINAL)

    async def observe(self, file_id: str) -> AsyncIterator[dict]:
        job = self._jobs.get(file_id)
        if job is None:  # already finished + evicted (very fast ingest)
            return
        while True:
            item = await job.queue.get()
            if item is _TERMINAL:
                return
            yield item

    async def shutdown(self) -> None:
        tasks = [j.task for j in list(self._jobs.values()) if j.task is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("ingest task errored during shutdown")


def reap_stranded(db) -> int:
    """Mark every KBFile stuck at status='indexing' as 'error'.

    In-process ingest tasks don't survive a process restart, so any 'indexing'
    row at startup is stranded. Recovery is the existing /reindex route.
    """
    from app.models import KBFile

    stranded = db.query(KBFile).filter(KBFile.status == "indexing").all()
    for f in stranded:
        f.status = "error"
        f.chunk_count = 0
    if stranded:
        db.commit()
    return len(stranded)
