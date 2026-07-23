# Decouple KB ingest from the connection + startup reaper — design

- **Date:** 2026-07-23
- **Status:** Draft (awaiting review)
- **Scope:** KB upload path only. Inline-chat attachments are explicitly out of scope.
- **Related:** follows the shipped cancellation-orphan fix (`ingest.py` `finally`, merge into main) and the PDFOxide swap (`2026-07-21-pdfoxide-extractor-swap-design.md`), which made in-process OCR synchronous and widened the interruption window. This is the "B + C" reliability pair deferred from that work.

## 1. Problem

KB upload runs `ingest()` **inline inside the SSE `StreamingResponse`** (`kb/routes.py::upload_file`), so the ingest is coupled to the client connection. On a refresh / network loss, Starlette cancels the streaming generator and the ingest dies mid-flight. The shipped cancellation-orphan fix made that failure *honest* (the file lands in `status="error"` instead of stranding at `"indexing"`), but the upload still **doesn't survive** the disconnect — the user must re-upload. With PDFOxide OCR now running synchronously in-process, a large scanned PDF is ~60s of work, widening the window where a refresh loses the upload.

Separately, a **hard crash/restart** mid-ingest leaves rows at `status="indexing"` that no longer have a running task — nothing reconciles them.

## 2. Decision

- **B — Decouple:** run KB ingest as a **detached, app-owned `asyncio` task** (an ingest-job registry on `app.state`). The upload's SSE response *observes* the task's progress but does not own it, so a client disconnect can't kill the ingest. Live progress is preserved while connected; after a disconnect the existing 2.5s `useKnowledgeBaseFiles` poll (`queries.ts:74-79`, spec §8.3) surfaces the final `ready`/`error`.
- **C — Reaper:** at startup, sweep any `status="indexing"` rows to `status="error"` (they're definitionally stranded — in-process tasks don't survive a restart).

**Decided forks (from brainstorming):** keep the live progress bar via a detached task + observed SSE (not fire-and-forget); KB-upload only (not inline attachments); poll-fallback on reconnect (no new reconnect endpoint); reaper marks-error (not requeue).

## 3. Goals / Non-goals

**Goals**
- A KB upload survives client disconnect: the ingest runs to completion regardless, DB reflects the true terminal state.
- Live `chunk_progress` while connected, using the **same SSE event shapes** as today (`chunk_progress` / `file_resolved` / `done` / `error`) — so the frontend happy path is unchanged.
- Bound concurrent ingests (one embedder / one Qdrant — don't thrash).
- Reaper reconciles crash-stranded `indexing` rows on startup.

**Non-goals (explicitly out)**
- **Inline-chat attachments** (`attachments.py`) — unchanged; already ghost-proofed by the shipped cancellation fix, and inline ingest has a readiness-before-chat ordering wrinkle that this design deliberately avoids.
- **A reconnect GET endpoint / live-progress resume after reconnect** — poll-fallback only.
- **Requeue-on-restart** — the reaper marks `error`; the existing `/reindex` route is the recovery path.
- No change to `ingest.py` itself, to the event shapes, or to retrieval.

## 4. Architecture — the ingest-job registry

New module `app/rag/ingest_jobs.py`. One registry instance is built at startup and stored on `app.state.ingest_jobs`; it owns the app-singleton deps and a concurrency semaphore.

```python
class IngestJob:
    file_id: str
    task: asyncio.Task            # the detached ingest driver
    queue: asyncio.Queue[dict]    # bounded; progress events + a terminal sentinel

class IngestJobRegistry:
    def __init__(self, *, session_factory, client, embedder, max_concurrent: int):
        self._jobs: dict[str, IngestJob] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._session_factory = session_factory   # app singletons, captured at startup
        self._client = client
        self._embedder = embedder

    def spawn(self, file_id: str) -> IngestJob: ...
    async def observe(self, file_id: str) -> AsyncIterator[dict]: ...
    async def shutdown(self) -> None: ...
```

### 4.1 Lifecycle

- **`spawn(file_id)`**: create the job with a bounded `asyncio.Queue`, `job.task = asyncio.create_task(self._run(job))`, store it in `self._jobs`, and attach `job.task.add_done_callback(lambda _: self._jobs.pop(file_id, None))`. **The dict entry is the GC anchor** that keeps the task alive after the request ends. Returns the job.
- **`_run(job)`**: `async with self._sem:` → open `bg_db = self._session_factory()` → `async for ev in ingest(bg_db, file_id, client=self._client, embedder=self._embedder): job.queue.put_nowait(ev)` (drop on `QueueFull`) → in `finally`, enqueue a terminal sentinel and `bg_db.close()`. This is the `reindex` background pattern (`kb/routes.py:195-207`) generalized. Exceptions from `ingest` are already handled inside `ingest` (status→error via its `finally`); `_run` logs and still enqueues the terminal sentinel.
- **`observe(file_id)`**: `await job.queue.get()` in a loop, `yield` each event, stop at the terminal sentinel. The observer holds its **own reference** to the job, so the `done`-callback evicting it from `self._jobs` mid-drain is harmless.
- **`shutdown()`**: cancel outstanding `job.task`s → each hits `ingest`'s cancellation `finally` (status→error); awaited best-effort so shutdown doesn't hang.

### 4.2 Sync primitive choice

A **bounded per-job `asyncio.Queue`, single consumer.** Because reconnect is poll-fallback (§3), a job has exactly one observer ever — no fan-out needed. `put_nowait` with a small `maxsize` and **drop-on-full**: progress is monotonic %, so a coalesced/jumped bar is acceptable, and a vanished observer cannot make the producer leak. The DB is the source of truth for the terminal state, so a dropped terminal *event* is only cosmetic.

### 4.3 Disconnect safety

The response's `observe()` generator is cancelled on disconnect; `_run`'s task is a **separate** task referenced by the registry, so it is untouched and runs to completion → DB flips to `ready`/`error` → the existing poll surfaces it.

## 5. Upload route refactor (`kb/routes.py::upload_file`)

Unchanged: validation, `save_upload`, `repo.create(... status defaults "indexing")`, `_resolve_kb_filing`. Then:

1. `job = request.app.state.ingest_jobs.spawn(file_id)` (replaces the inline `ingest` call).
2. Return `StreamingResponse(_stream())` where `_stream()` does `async for ev in request.app.state.ingest_jobs.observe(file_id): yield sse(ev)`, then emits the existing `file_resolved` + `done` (re-fetch the `KBFile` for `file_resolved`, as today). The `except` that emits an `error` event stays.

The `client`/`embedder`/`session_factory` DI params drop off `upload_file` (the registry holds them) — the route needs only `request` (or an injected registry dependency) plus the existing auth/db/form params.

## 6. Reaper (C)

`reap_stranded(db) -> int` in `app/rag/ingest_jobs.py`: `UPDATE kb_files SET status='error', chunk_count=0 WHERE status='indexing'`, return the count. Called from `main.py` `lifespan` **after** migrations + admin bootstrap and **before** `yield`, wrapped in try/except like admin bootstrap (never crashes startup); logs the count. Rationale: in-process tasks don't survive a restart, so every `indexing` row at startup is stranded. Recovery is the existing `/reindex` route.

## 7. Startup / shutdown wiring (`main.py` lifespan)

- Before `yield`: build the registry with the app singletons and store it —
  `app.state.ingest_jobs = IngestJobRegistry(session_factory=get_session_factory(), client=get_client(), embedder=get_embedder(), max_concurrent=settings.max_concurrent_ingests)` (imports: `app.rag.vectors.get_client`, `app.rag.embedder.get_embedder`, `app.db.get_session_factory`). Then `reap_stranded(SessionLocal())`.
- After `yield` (shutdown): `await app.state.ingest_jobs.shutdown()`.

## 8. Config

- `max_concurrent_ingests: int = 2` (semaphore) — one embedder / one Qdrant; small default.
- Queue `maxsize` is an internal constant (e.g. 64), not a config knob (YAGNI).

## 9. Frontend impact

**None required for correctness.** The SSE event shapes are identical, so the upload progress bar (`UploadTaskCard`) works unchanged while connected, and the existing `useKnowledgeBaseFiles` poll (`queries.ts:74-79`) already flips a disconnected upload's file to `ready`/`error`. The transient upload card is local state (lost on refresh, as today); the KB list is the durable view. No new endpoints, no query changes.

## 10. Testing

- **The money test:** spawn a job, start observing, cancel the observer mid-run (simulating disconnect), and assert the **task still completes** and the `KBFile` ends `status="ready"` with chunks in Qdrant. This is the disconnect-survival proof (mirrors the existing cancellation-orphan test's `athrow` technique, but asserts survival rather than error).
- **Registry unit:** `spawn` stores then evicts on completion; `observe` yields the progress events then the terminal; a second concurrent spawn respects the semaphore (`max_concurrent=1` → second waits).
- **Reaper:** seed two `indexing` + one `ready` KBFile, call `reap_stranded`, assert the two flip to `error` (chunk_count 0) and the `ready` is untouched; returns 2.
- **Route:** upload streams `chunk_progress` → `file_resolved` → `done`; file resolves to `ready` (reuse the in-memory Qdrant + fake/real embedder fixtures from `test_ingest.py`).
- Full suite stays green (currently 339 passed, 1 Docker-skip).

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Detached task GC'd before running | Registry dict holds a strong ref for the task's lifetime; evict only in the `done` callback. |
| Unbounded queue if observer vanishes | Bounded queue + `put_nowait` drop-on-full; DB is source of truth. |
| App shutdown mid-ingest | `registry.shutdown()` cancels → `ingest` `finally` marks error; reaper covers hard crashes. |
| Concurrency thrash (many uploads) | `max_concurrent_ingests` semaphore (default 2). |
| Event-loop blocking during sync OCR | Pre-existing (unchanged by this work); the task still blocks its coroutine, but concurrent requests are served — decoupling does not make it worse. A future `asyncio.to_thread` for extract is a separate optimization. |

## 12. Follow-ups (not built here)

- Inline-chat attachment decoupling (readiness-before-chat design).
- Reconnect GET progress endpoint / live-resume, if the poll-fallback UX proves insufficient.
- `asyncio.to_thread` around the synchronous extract/OCR so a long ingest doesn't block its worker at all.
