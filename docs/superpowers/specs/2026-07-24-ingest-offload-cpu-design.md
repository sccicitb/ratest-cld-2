# Offload ingest CPU work off the event loop — design

- **Date:** 2026-07-24
- **Status:** Draft (awaiting review)
- **Scope:** `app/rag/ingest.py` only. The `to_thread` perf follow-up flagged in the B+C spec (§12) and final review.
- **Related:** ingest decouple + reaper (`2026-07-23-ingest-decouple-reaper-design.md`); PDFOxide swap (synchronous in-process OCR).

## 1. Problem

`ingest()` does two blocking, CPU/GPU-bound calls **synchronously on the event loop**:
- `ingest.py:52` — `extract_text(...)` (PDFOxide/PaddleOCR OCR; ~60s for a large scanned PDF).
- `ingest.py:93` — `upsert_chunks(client, embedder, batch)` per batch (BGE-M3 embedding + Qdrant upsert; the *repeated* blocker).

The B+C work moved ingest into a detached task, but that task's coroutine still runs this work on the loop — so a long ingest **stalls the whole worker** (every other request waits), and it's the residual rough edge the B+C final review flagged for a self-hosted single-worker deployment.

## 2. Decision

Run both heavy calls in a **single shared, single-worker `ThreadPoolExecutor`** via `loop.run_in_executor`, so the event loop is freed at each `await` while the CPU work runs on a background thread.

**Why one worker (not `asyncio.to_thread`'s default multi-worker pool):**
- **Correct for one box:** OCR and BGE-M3 are CPU/GPU-bound; running two ingests' work in parallel threads thrashes shared compute — it's slower, not faster. Serial is the right execution model regardless.
- **Safe:** the PDFOxide OCR engine (that `ort` Mutex) and the shared BGE-M3 model have no proven thread-safety for concurrent calls. One worker means no concurrent access.
- The goal is "don't block the loop," not "parallelize CPU work" — a single worker achieves the former without the risks of the latter.

## 3. Goals / Non-goals

**Goals**
- The event loop is not blocked during extraction or embedding — concurrent requests are served while an ingest runs.
- Both `extract_text` and `upsert_chunks` run off the loop.
- No behavior change: same chunks, same status transitions, same `chunk_progress`/error semantics.

**Non-goals**
- No parallelism of CPU work (single worker by design).
- No change to `ingest`'s contract, the registry, retrieval, or the embedder/extract internals.
- No config knob for worker count (YAGNI — one worker is the correct value).

## 4. Design

In `app/rag/ingest.py`, add a module-level executor:

```python
from concurrent.futures import ThreadPoolExecutor

# Single worker: serializes CPU/GPU-bound ingest work (OCR, BGE-M3) — correct on
# one box, and sidesteps the unproven thread-safety of the shared OCR engine /
# embedder — while keeping it off the event loop. Process-lived; atexit reaps it.
_CPU_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest-cpu")
```

In `ingest()`, wrap the two calls (using the running loop):

```python
    loop = asyncio.get_running_loop()
    ...
    text = await loop.run_in_executor(_CPU_EXECUTOR, extract_text, file.storage_key, file.name)
    ...
    for start in range(0, total, _EMBED_BATCH):
        batch = chunks[start : start + _EMBED_BATCH]
        await loop.run_in_executor(_CPU_EXECUTOR, upsert_chunks, client, embedder, batch)
        ...
```

**DB safety:** neither wrapped call touches the SQLAlchemy `db` session — `db.commit()` / status writes stay on the main thread in the async context. No cross-thread session use.

**Exceptions:** an exception raised inside the thread propagates out of `run_in_executor` to the awaiting coroutine, so `ingest`'s existing `finally` still marks the file `error`. Unchanged.

**Cancellation (bonus):** if the ingest task is cancelled (app shutdown) while awaiting `run_in_executor`, the coroutine raises `CancelledError` promptly and its `finally` runs, while the orphaned worker thread finishes its current call in the background (result discarded). This actually *softens* the B+C "shutdown has no timeout" edge — the coroutine no longer blocks on the sync call.

**Lifecycle:** the executor is a process-lived module singleton; Python's `atexit` shuts it down on exit. No explicit lifespan wiring (a single-worker daemon is fine; adding an explicit shutdown that `wait=True`s on a running 60s OCR would only slow shutdown).

## 5. Testing

- **Offload proof:** a test that monkeypatches `extract_text` to capture `threading.current_thread()` and asserts it is **not** `threading.main_thread()` when driven through `ingest` — proving extraction runs off the event-loop thread. Same technique for `upsert_chunks` (capture thread in a fake embedder/upsert).
- **Regression:** the existing `test_ingest.py` end-to-end tests (real extract → chunk → embed → upsert → status `ready`, and the error/cancellation paths) stay green — proving the wrapped calls still produce identical results.
- **Full suite:** stays green (currently 345 passed, 1 Docker-skip).

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Cross-thread DB use | Neither wrapped call touches `db`; verified in the design. |
| Executor never reclaimed | Module singleton + `atexit`; single worker, negligible footprint. |
| Serialization hurts throughput | Intended — CPU work should be serial on one box; the semaphore already bounded concurrency to 2 and the loop-block already serialized this work. Net change is only "off the loop." |

## 7. Follow-ups (not built here)

- If a future multi-GPU / multi-node deployment wants parallel ingest CPU work, revisit worker count (and prove OCR/embedder thread-safety first).
