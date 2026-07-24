# Decouple KB Ingest + Startup Reaper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run KB ingest as a detached, app-owned asyncio task so a client disconnect can't kill an upload, and add a startup reaper that reconciles crash-stranded `indexing` rows.

**Architecture:** A new `IngestJobRegistry` (on `app.state.ingest_jobs`, built at startup with the app-singleton Qdrant client / embedder / session factory) owns per-upload `asyncio` tasks and a bounded per-job progress queue. The upload route calls `spawn(file_id)` then streams `observe(file_id)` — the SSE response *observes* the task but doesn't own it, so a disconnect cancels only the observer while the task runs to completion (DB → ready/error, surfaced by the existing 2.5s poll). A `reap_stranded(db)` sweep in `lifespan` marks leftover `indexing` rows as `error`.

**Tech Stack:** Python 3.10, FastAPI/Starlette, asyncio, SQLAlchemy, Qdrant, pytest, uv.

## Global Constraints

- Scope is **KB upload only**. Do NOT touch `attachments.py` (inline-chat attachments), `ingest.py`, retrieval, or event shapes.
- The SSE event shapes stay identical: `chunk_progress` / `file_resolved` / `done` / `error`. Frontend needs no change.
- `extract_text`/`ingest` are unchanged; the shipped cancellation `finally` in `ingest.py` still owns status-on-abort.
- Reconnect is **poll-fallback** — no new reconnect endpoint. Reaper **marks-error** — no requeue.
- The registry is a **process singleton** (task refs must outlive the request); never per-request.
- Run all commands from `backend/`. Prefix pytest with `env -u VIRTUAL_ENV uv run`.
- Spec: `docs/superpowers/specs/2026-07-23-ingest-decouple-reaper-design.md`.

---

## File Structure

- `backend/app/rag/ingest_jobs.py` — **new**: `IngestJob`, `IngestJobRegistry` (`spawn`/`observe`/`shutdown`), `reap_stranded`, `_offer` (Task 1).
- `backend/app/config.py` — **modify**: add `max_concurrent_ingests` (Task 1).
- `backend/app/kb/routes.py` — **modify**: add `get_ingest_jobs` DI seam; refactor `upload_file` to spawn+observe (Task 2).
- `backend/app/main.py` — **modify**: build registry on `app.state` + call reaper (startup); `shutdown()` (Task 2).
- `backend/tests/test_ingest_jobs.py` — **new**: registry + disconnect-survival + reaper tests (Task 1).
- `backend/tests/test_kb.py` — **modify**: autouse fixture overrides `get_ingest_jobs` (Task 2).

---

### Task 1: Ingest-job registry module + reaper + config

Pure new module, unit-tested in isolation (no route/app wiring). Non-breaking — nothing imports it yet.

**Files:**
- Create: `backend/app/rag/ingest_jobs.py`
- Modify: `backend/app/config.py` (add one field)
- Test: `backend/tests/test_ingest_jobs.py` (create)

**Interfaces:**
- Produces:
  - `IngestJobRegistry(*, session_factory, client, embedder, max_concurrent: int)` with `spawn(file_id: str) -> IngestJob`, `async observe(file_id: str) -> AsyncIterator[dict]`, `async shutdown() -> None`.
  - `IngestJob` with `.file_id: str`, `.task: asyncio.Task | None`, `.queue: asyncio.Queue`.
  - `reap_stranded(db) -> int`.
- Consumes: `app.rag.ingest.ingest` (existing async generator), `settings.max_concurrent_ingests`.

- [ ] **Step 1: Add the config field**

In `backend/app/config.py`, after the OCR block, add:

```python
    # --- Ingest jobs: max concurrent detached ingest tasks (one embedder/Qdrant) ---
    max_concurrent_ingests: int = 2
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_ingest_jobs.py`. Reuse the in-memory-SQLite + in-memory-Qdrant + fake-embedder patterns from `tests/test_ingest.py`.

```python
"""Tests for the detached ingest-job registry + startup reaper."""
from __future__ import annotations

import asyncio

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import KBFile, User
from app.rag.ingest_jobs import IngestJobRegistry, reap_stranded


@pytest.fixture()
def engine_factory(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return factory


class _FakeEmbedder:
    def embed_passages(self, texts):
        return [{"dense": [0.1] * 1024, "sparse": {"indices": [1], "values": [1.0]}} for _ in texts]

    def embed_query(self, text):
        return {"dense": [0.1] * 1024, "sparse": {"indices": [1], "values": [1.0]}}


def _make_file(db, storage_key, tmp_path, text="Hello world. " * 300) -> KBFile:
    (tmp_path / storage_key).write_text(text, encoding="utf-8")
    user = User(email=f"{storage_key}@t.com", display_name="U", password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    f = KBFile(
        user_id=user.id, scope="kb", session_id=None, name=storage_key, size=100,
        storage_key=storage_key, status="indexing", chunk_count=0, tags=[],
        is_public=True, group_id=None,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def test_task_survives_observer_cancellation(engine_factory, tmp_path):
    """The money test: an observer that stops mid-stream (client disconnect) must
    NOT kill the detached task — it runs to completion and the file lands ready."""
    qdrant = QdrantClient(":memory:")

    async def _run():
        db = engine_factory()
        file = _make_file(db, "doc.txt", tmp_path)
        reg = IngestJobRegistry(
            session_factory=engine_factory, client=qdrant, embedder=_FakeEmbedder(),
            max_concurrent=2,
        )
        job = reg.spawn(file.id)
        agen = reg.observe(file.id)
        await agen.__anext__()      # consume one progress event
        await agen.aclose()         # stop observing = client disconnect
        await job.task              # detached task must still finish
        db.expire_all()
        refreshed = db.get(KBFile, file.id)
        return refreshed.status, refreshed.chunk_count

    status, chunks = asyncio.run(_run())
    assert status == "ready"
    assert chunks > 0


def test_observe_yields_progress_then_terminates(engine_factory, tmp_path):
    qdrant = QdrantClient(":memory:")

    async def _run():
        db = engine_factory()
        file = _make_file(db, "doc.txt", tmp_path)
        reg = IngestJobRegistry(
            session_factory=engine_factory, client=qdrant, embedder=_FakeEmbedder(),
            max_concurrent=2,
        )
        reg.spawn(file.id)
        events = [ev async for ev in reg.observe(file.id)]
        await asyncio.sleep(0)  # let the done-callback run
        return events, file.id in reg._jobs

    events, still_registered = asyncio.run(_run())
    assert events and all(ev["type"] == "chunk_progress" for ev in events)
    assert still_registered is False  # evicted on completion


def test_reap_stranded_marks_indexing_error(engine_factory):
    db = engine_factory()
    u = User(email="r@t.com", display_name="U", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    ids = {}
    for name, st in [("a", "indexing"), ("b", "indexing"), ("c", "ready")]:
        f = KBFile(user_id=u.id, scope="kb", session_id=None, name=name, size=1,
                   storage_key=name, status=st, chunk_count=(5 if st == "ready" else 0),
                   tags=[], is_public=True, group_id=None)
        db.add(f); db.commit(); db.refresh(f)
        ids[name] = f.id

    n = reap_stranded(db)

    assert n == 2
    assert db.get(KBFile, ids["a"]).status == "error"
    assert db.get(KBFile, ids["b"]).status == "error"
    assert db.get(KBFile, ids["c"]).status == "ready"  # untouched
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_ingest_jobs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.ingest_jobs'`.

- [ ] **Step 4: Implement the module**

Create `backend/app/rag/ingest_jobs.py`:

```python
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
        # The dict entry is the GC anchor; evict when the task finishes.
        job.task.add_done_callback(lambda _t, fid=file_id: self._jobs.pop(fid, None))
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
            except (asyncio.CancelledError, Exception):
                pass


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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_ingest_jobs.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/rag/ingest_jobs.py backend/app/config.py backend/tests/test_ingest_jobs.py
git commit -m "feat(rag): detached ingest-job registry + startup reaper (unit-tested)

IngestJobRegistry owns detached asyncio ingest tasks (bounded per-job progress
queue, concurrency semaphore) so an observer cancellation (client disconnect)
can't kill the task. reap_stranded marks crash-stranded 'indexing' rows error.
Money test proves task survives observer cancellation and file lands ready."
```

---

### Task 2: Wire the registry into the upload route + lifespan

**Files:**
- Modify: `backend/app/kb/routes.py` (add `get_ingest_jobs` seam; refactor `upload_file`)
- Modify: `backend/app/main.py` (build registry on `app.state` + reaper at startup; `shutdown()`)
- Modify: `backend/tests/test_kb.py` (autouse fixture overrides `get_ingest_jobs`)

**Interfaces:**
- Consumes: `IngestJobRegistry`, `reap_stranded` (Task 1); `settings.max_concurrent_ingests`.
- Produces: `get_ingest_jobs(request) -> IngestJobRegistry` DI seam; `app.state.ingest_jobs`.

- [ ] **Step 1: Add the `get_ingest_jobs` DI seam + refactor `upload_file`**

In `backend/app/kb/routes.py`, in the "DI seams" block (near `get_qdrant`), add:

```python
from fastapi import Request

from app.rag.ingest_jobs import IngestJobRegistry


def get_ingest_jobs(request: Request) -> IngestJobRegistry:
    return request.app.state.ingest_jobs


IngestJobsDep = Annotated[IngestJobRegistry, Depends(get_ingest_jobs)]
```

Replace the `upload_file` signature's `client: QdrantDep, embedder: EmbedderDep` params with `ingest_jobs: IngestJobsDep` (keep `user`, `db`, `file`, and the `group_id`/`is_public`/`tags` Form params). Replace the `_stream()` body (the inline `ingest` loop) with spawn + observe:

```python
    file_id = kb_file.id
    ingest_jobs.spawn(file_id)

    async def _stream():
        async for event in ingest_jobs.observe(file_id):
            yield sse(event)
        # Task finished (or was never observed). Re-read the file for its terminal state.
        db.refresh(kb_file)
        if kb_file.status == "error":
            yield sse({"type": "error", "message": "Ingestion failed"})
            return
        out = KnowledgeBaseFileOut.model_validate(kb_file)
        yield sse({"type": "file_resolved", "file": out.model_dump(mode="json", by_alias=True)})
        yield sse({"type": "done"})

    return StreamingResponse(_stream(), media_type="text/event-stream")
```

(Everything before `file_id = kb_file.id` — validation, `save_upload`, `repo.create`, `_resolve_kb_filing` — is unchanged.)

- [ ] **Step 2: Build the registry + run the reaper in `lifespan`**

In `backend/app/main.py`'s `lifespan`, after the `ensure_ort_dylib()` block and before `yield`, add:

```python
    # Ingest-job registry (detached, disconnect-surviving KB ingest) + reaper.
    from app.db import SessionLocal, get_session_factory
    from app.rag.embedder import get_embedder
    from app.rag.ingest_jobs import IngestJobRegistry, reap_stranded
    from app.rag.vectors import get_client

    app.state.ingest_jobs = IngestJobRegistry(
        session_factory=get_session_factory(),
        client=get_client(),
        embedder=get_embedder(),
        max_concurrent=settings.max_concurrent_ingests,
    )
    try:
        _rdb = SessionLocal()
        try:
            reaped = reap_stranded(_rdb)
            if reaped:
                log.info("Reaper: marked %d stranded 'indexing' file(s) as error.", reaped)
        finally:
            _rdb.close()
    except Exception:
        log.exception("Startup reaper failed — continuing.")
```

After `yield` (shutdown), add:

```python
    await app.state.ingest_jobs.shutdown()
```

(Note: building the registry calls `get_embedder()`, which eagerly loads BGE-M3 at startup instead of on first upload — deliberate: fail-fast and no first-upload latency spike. `settings` and `log` are already in scope in `main.py`.)

- [ ] **Step 3: Update the `test_kb.py` autouse fixture to provide the registry**

In `backend/tests/test_kb.py`, add to the imports:

```python
from app.kb.routes import get_ingest_jobs
from app.rag.ingest_jobs import IngestJobRegistry
```

In the autouse fixture that sets `app.dependency_overrides` (currently overriding `get_qdrant`/`get_embedder_dep`/`get_session_factory`), add an override that builds one test registry from the same fakes, and clean it up:

```python
    _test_registry = IngestJobRegistry(
        session_factory=session_factory, client=qdrant_memory,
        embedder=_FakeEmbedder(), max_concurrent=2,
    )
    app.dependency_overrides[get_ingest_jobs] = lambda: _test_registry
```
```python
    app.dependency_overrides.pop(get_ingest_jobs, None)
```

(Use the same `qdrant_memory` / `session_factory` / `_FakeEmbedder` the fixture already wires for the other overrides — match their exact names in that file.)

- [ ] **Step 4: Run the KB + ingest-jobs suites**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_kb.py tests/test_shared_kb.py tests/test_ingest_jobs.py -q`
Expected: PASS — existing upload tests still green through the registry; new registry tests green. If an existing upload test asserted an exact inline-ingest behavior that changed (e.g. an error surfaced via exception vs the new `status=="error"` check), update it to assert the SSE `error` event / final `status`.

- [ ] **Step 5: App import + lifespan smoke**

Run: `env -u VIRTUAL_ENV uv run python -c "import app.main; print('ok')"`
Expected: prints `ok` (no import/wiring errors).

- [ ] **Step 6: Commit**

```bash
git add backend/app/kb/routes.py backend/app/main.py backend/tests/test_kb.py
git commit -m "feat(kb): run KB upload ingest via the detached registry; wire reaper at startup

upload_file now spawns a detached ingest task and streams observe() (same SSE
event shapes), so a client disconnect no longer kills the ingest — the task
finishes and the existing poll surfaces ready/error. lifespan builds the
registry on app.state, runs the startup reaper, and shuts tasks down cleanly."
```

---

### Task 3: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `env -u VIRTUAL_ENV uv run pytest -q`
Expected: PASS — prior baseline (339 passed, 1 Docker-skip) plus the new `test_ingest_jobs.py` (3), **0 failures**. (~15 min; sandbox Docker test still skips.)

- [ ] **Step 2: Confirm branch state**

Run: `git log --oneline main..HEAD`
Expected: the spec commit + the two task commits on `feat/ingest-decouple-reaper`, ready to merge.

---

## Self-Review

**Spec coverage:**
- §4 registry (`IngestJob`/`IngestJobRegistry`, spawn/observe/shutdown, dict-anchor, bounded queue, semaphore) → Task 1. ✅
- §4.3 disconnect safety → Task 1 money test (`test_task_survives_observer_cancellation`). ✅
- §5 upload route refactor (spawn+observe, same event shapes, error-via-status) → Task 2, Step 1. ✅
- §6 reaper (`reap_stranded`, mark-error) → Task 1 (impl+test) + Task 2 (lifespan call). ✅
- §7 startup/shutdown wiring (registry on app.state, reaper, shutdown) → Task 2, Step 2. ✅
- §8 config (`max_concurrent_ingests`) → Task 1, Step 1. ✅
- §9 frontend impact (none) → no task; event shapes preserved in Task 2. ✅
- §10 testing (money test, registry unit, reaper, route) → Task 1 tests + Task 2 Step 4 (route via existing test_kb upload tests). ✅
- DI-seam-overridable-in-tests (integration reality) → `get_ingest_jobs` seam + test_kb override (Task 2 Steps 1, 3). ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. The one instruction to "match the exact fixture names in that file" (Task 2 Step 3) is a deliberate follow-existing-names directive, not a placeholder — the names to use (`qdrant_memory`, `session_factory`, `_FakeEmbedder`) are given.

**Type consistency:** `IngestJobRegistry(*, session_factory, client, embedder, max_concurrent)`, `spawn(file_id) -> IngestJob`, `observe(file_id) -> AsyncIterator[dict]`, `shutdown()`, `reap_stranded(db) -> int`, `get_ingest_jobs(request) -> IngestJobRegistry` used consistently across Tasks 1–2. `settings.max_concurrent_ingests` produced in Task 1, consumed in Task 2. ✅
