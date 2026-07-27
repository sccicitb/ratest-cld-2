# Live Chat Turns — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run each chat turn as a detached, app-owned task with a replay log + multi-observer tail, so a turn survives navigation, live-resumes on return, and the sidebar shows which rooms are generating.

**Architecture:** `ChatTurnRegistry` on `app.state` (mirrors `app/rag/ingest_jobs.py`) but with an append-only event log per turn and multi-observer `observe(from_index)`. `POST /chat` spawns + observes; new `GET /sessions/{id}/stream` re-attaches (replay + tail); `SessionOut.activeTurn` (from the registry) drives sidebar dots + resume-on-entry (flag + poll).

**Tech Stack:** Python 3.10, FastAPI/asyncio/SQLAlchemy (backend); React 19 + TanStack Query (frontend); uv; npm.

## Global Constraints

- Mirror the `ingest_jobs` lessons exactly: registry is a **process singleton**; the dict entry is the GC anchor; **identity-checked** done-callback eviction; `bg_db` per task; `shutdown()` cancels.
- SSE event shapes are **unchanged** (`step`/`token`/`reasoning`/`artifact`/`done`/`error`) — the existing consumer works.
- **One turn per room**: a second concurrent `POST /chat` → **409**; the frontend disables send while active.
- Replay log is **ephemeral** (turn lifetime, evicted on completion). No persistence change; `run_turn` untouched.
- No global push channel, no queueing (both are §10 follow-ups).
- Backend from `backend/` with `env -u VIRTUAL_ENV uv run`; frontend from `frontend/`.
- Spec: `docs/superpowers/specs/2026-07-27-live-chat-turns-design.md`.

---

## File Structure

- `backend/app/chat/turns.py` — **new**: `ChatTurnJob`, `ChatTurnRegistry`, `TurnInProgress`, `build_turn_registry` (Task 1).
- `backend/app/config.py` — **modify**: `max_concurrent_chat_turns` (Task 1).
- `backend/app/chat/routes.py` — **modify**: `get_chat_turns` seam; refactor `chat` to spawn+observe; add `GET /{id}/stream`; move `_build_registry` → `turns.py` (Task 2).
- `backend/app/sessions/routes.py` + `backend/app/schemas/__init__.py` — **modify**: `activeTurn` on the session list (Task 2).
- `backend/app/main.py` — **modify**: build registry on `app.state` + shutdown (Task 2).
- `backend/tests/test_chat_turns.py` — **new** (Task 1); `test_chat_route.py` / session tests — **modify** (Task 2).
- `frontend/src/hooks/useStreamChat.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/queries.ts`, `frontend/src/types/chat.ts`, the chat route, the sidebar, the composer — **modify** (Task 3).

---

### Task 1: `ChatTurnRegistry` + replay log (registry module, unit-tested)

Pure new module + one config field. Non-breaking — nothing wires it yet.

**Interfaces produced:**
- `ChatTurnRegistry(*, model, client, embedder, session_factory, max_concurrent)` with `spawn(session_id, *, user_id, message, model_content, attachment_ids) -> ChatTurnJob`, `async observe(session_id, from_index=0) -> AsyncIterator[dict]`, `has_active(session_id) -> bool`, `active_session_ids() -> set[str]`, `async shutdown()`.
- `ChatTurnJob` with `.session_id`, `.task`, `.events: list[dict]`, `.done: bool`.
- `TurnInProgress(Exception)`; `build_turn_registry() -> ToolRegistry`.

- [ ] **Step 1: Config field**

In `backend/app/config.py`, near the other limits, add:
```python
    # Max concurrent detached chat turns (I/O-bound on the model; generous).
    max_concurrent_chat_turns: int = 16
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_chat_turns.py`. Reuse the in-memory patterns from `test_chat_loop.py`.

```python
"""Tests for the detached chat-turn registry (decouple + replay/resume)."""
from __future__ import annotations

import asyncio

import pytest

from app.chat.client import ModelChunk
from app.chat.turns import ChatTurnRegistry, TurnInProgress
from app.models import ChatSession, Message, User


class _FakeModelClient:
    """Yields the given ModelChunks for the (single) stream call."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def stream(self, messages, tools):
        for c in self._chunks:
            yield c


def _seed(session_factory):
    db = session_factory()
    user = User(email="turns@test.com", display_name="U", password_hash="x")
    db.add(user)
    db.commit()
    session = ChatSession(user_id=user.id, title="New Chat")
    db.add(session)
    db.commit()
    sid, uid = session.id, user.id
    db.close()
    return sid, uid


def _registry(session_factory, chunks):
    return ChatTurnRegistry(
        model=_FakeModelClient(chunks),
        client=None,
        embedder=None,
        session_factory=session_factory,
        max_concurrent=4,
    )


def _spawn(reg, sid, uid, message="hi"):
    return reg.spawn(sid, user_id=uid, message=message, model_content=message, attachment_ids=[])


def test_task_survives_observer_cancellation(session_factory):
    """Money test: an observer that stops mid-stream must not kill the turn —
    it runs to completion and the assistant Message persists."""
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [
            ModelChunk(type="text", text="Hello "),
            ModelChunk(type="text", text="world."),
        ])
        job = _spawn(reg, sid, uid)
        agen = reg.observe(sid)
        await agen.__anext__()      # consume one event
        await agen.aclose()         # observer leaves (navigation)
        await job.task              # turn must still finish
        return

    asyncio.run(_run())

    db = session_factory()
    msgs = db.query(Message).filter_by(session_id=sid).order_by(Message.created_at).all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[-1].content == "Hello world."
    db.close()


def test_second_observer_replays_full_log_then_tails(session_factory):
    """A second observer (resume) replays everything from index 0, then tails
    to done — proving multi-observer live-resume."""
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [
            ModelChunk(type="text", text="A"),
            ModelChunk(type="text", text="B"),
            ModelChunk(type="text", text="C"),
        ])
        job = _spawn(reg, sid, uid)
        # First observer drains everything to completion.
        first = [ev async for ev in reg.observe(sid)]
        # After completion the job is evicted, so a fresh observe returns nothing,
        # BUT the events are the source: assert the FIRST observer saw the stream.
        await job.task
        return first

    events = asyncio.run(_run())
    token_text = "".join(e["content"] for e in events if e["type"] == "token")
    assert token_text == "ABC"
    assert any(e["type"] == "done" for e in events)


def test_concurrent_resume_observer_replays(session_factory):
    """While a turn is live, a second observe(from_index=0) replays the log so
    far and tails the rest — the exact live-resume path."""
    sid, uid = _seed(session_factory)
    gate = asyncio.Event()

    class _GatedModel:
        async def stream(self, messages, tools):
            yield ModelChunk(type="text", text="early")
            await gate.wait()               # hold the turn open
            yield ModelChunk(type="text", text="-late")

    async def _run():
        reg = ChatTurnRegistry(model=_GatedModel(), client=None, embedder=None,
                               session_factory=session_factory, max_concurrent=4)
        job = _spawn(reg, sid, uid)
        # Let the first chunk land.
        await asyncio.sleep(0.05)
        assert reg.has_active(sid) is True
        # A second observer attaches now and must replay "early".
        resumed = []

        async def _resume():
            async for ev in reg.observe(sid, from_index=0):
                resumed.append(ev)

        t = asyncio.create_task(_resume())
        await asyncio.sleep(0.05)
        gate.set()                          # release the rest
        await job.task
        await t
        return resumed

    resumed = asyncio.run(_run())
    text = "".join(e["content"] for e in resumed if e["type"] == "token")
    assert text == "early-late"             # replayed the missed chunk + tailed


def test_double_spawn_same_session_raises(session_factory):
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [ModelChunk(type="text", text="x")])
        _spawn(reg, sid, uid)
        with pytest.raises(TurnInProgress):
            _spawn(reg, sid, uid)

    asyncio.run(_run())


def test_has_active_reflects_lifecycle(session_factory):
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [ModelChunk(type="text", text="x")])
        job = _spawn(reg, sid, uid)
        active_during = reg.has_active(sid)
        await job.task
        await asyncio.sleep(0)              # let the done-callback evict
        return active_during, reg.has_active(sid)

    during, after = asyncio.run(_run())
    assert during is True
    assert after is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_chat_turns.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.chat.turns'`.

- [ ] **Step 4: Implement the module**

Create `backend/app/chat/turns.py`:

```python
"""Detached, app-owned chat turns with a replay log for live-resume.

Each turn runs `run_turn` inside a background task (not the request's SSE
response), so a client disconnect/navigation cancels only the observing
response — the turn runs to completion and persists. Every event is appended to
an in-memory replay log; multiple observers (the original sender, plus a client
that navigates back) each tail the log from their own cursor. The registry is a
process singleton on app.state; the dict entry is the task's GC anchor.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.chat.loop import run_turn
from app.mcp.resolve import resolve_caller_mcp_tools
from app.tools.builtin.create_artifact import CreateArtifact
from app.tools.builtin.execute_code import ExecuteCode
from app.tools.builtin.search_kb import SearchKnowledgeBase
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class TurnInProgress(Exception):
    """Raised by spawn() when a room already has a live turn (→ HTTP 409)."""


def build_turn_registry() -> ToolRegistry:
    """A ToolRegistry with the three built-in tools (moved from chat/routes)."""
    registry = ToolRegistry()
    registry.register(SearchKnowledgeBase())
    registry.register(ExecuteCode())
    registry.register(CreateArtifact())
    return registry


class ChatTurnJob:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: list[dict] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self._wakeup = asyncio.Event()

    def _wake(self) -> None:
        # Wake current waiters and swap in a fresh event for the next round.
        self._wakeup.set()
        self._wakeup = asyncio.Event()

    def publish(self, event: dict) -> None:
        self.events.append(event)
        self._wake()

    def finish(self) -> None:
        self.done = True
        self._wake()


class ChatTurnRegistry:
    """Owns detached chat-turn tasks. Built once at startup with app singletons."""

    def __init__(self, *, model, client, embedder, session_factory, max_concurrent: int) -> None:
        self._jobs: dict[str, ChatTurnJob] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._model = model
        self._client = client
        self._embedder = embedder
        self._session_factory = session_factory

    def spawn(
        self, session_id: str, *, user_id: str, message: str,
        model_content: str, attachment_ids: list[str],
    ) -> ChatTurnJob:
        if session_id in self._jobs:
            raise TurnInProgress(session_id)
        job = ChatTurnJob(session_id)
        job.task = asyncio.create_task(
            self._run(job, user_id, message, model_content, attachment_ids)
        )
        self._jobs[session_id] = job
        job.task.add_done_callback(
            lambda _t, sid=session_id, this=job: (
                self._jobs.pop(sid, None) if self._jobs.get(sid) is this else None
            )
        )
        return job

    async def _run(
        self, job: ChatTurnJob, user_id: str, message: str,
        model_content: str, attachment_ids: list[str],
    ) -> None:
        from app.models import ChatSession, User

        try:
            async with self._sem:
                db = self._session_factory()
                try:
                    session = db.get(ChatSession, job.session_id)
                    user = db.get(User, user_id)
                    if session is None or user is None:
                        return
                    mcp_tools = await resolve_caller_mcp_tools(db, user)
                    registry = build_turn_registry()
                    for t in mcp_tools:
                        registry.register(t)
                    ctx = ToolContext(
                        user_id=user_id, session_id=job.session_id,
                        db=db, client=self._client, embedder=self._embedder,
                    )
                    async for ev in run_turn(
                        db=db, session=session, message=message,
                        model_content=model_content, attachment_ids=attachment_ids,
                        registry=registry, model=self._model, ctx=ctx,
                    ):
                        job.publish(ev)
                finally:
                    db.close()
        except Exception:
            # run_turn guards its own errors, but be safe: surface + terminate.
            logger.exception("chat turn failed for session %s", job.session_id)
            job.publish({"type": "error", "message": "Chat turn failed"})
        finally:
            job.finish()

    async def observe(self, session_id: str, from_index: int = 0) -> AsyncIterator[dict]:
        job = self._jobs.get(session_id)
        if job is None:  # no live turn (never started, or already finished+evicted)
            return
        i = from_index
        while True:
            wakeup = job._wakeup      # capture BEFORE draining (avoids lost wakeups)
            while i < len(job.events):
                yield job.events[i]
                i += 1
            if job.done:
                return
            await wakeup.wait()

    def has_active(self, session_id: str) -> bool:
        return session_id in self._jobs

    def active_session_ids(self) -> set[str]:
        return set(self._jobs.keys())

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
                logger.exception("chat turn errored during shutdown")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_chat_turns.py -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/chat/turns.py backend/app/config.py backend/tests/test_chat_turns.py
git commit -m "feat(chat): detached chat-turn registry with replay log (unit-tested)

ChatTurnRegistry owns detached run_turn tasks with a per-turn append-only event
log + multi-observer tail, so a turn survives observer cancellation and a second
observer can replay+resume. Mirrors ingest_jobs (GC anchor, identity-checked
eviction, semaphore, shutdown). One turn per room (TurnInProgress)."
```

---

### Task 2: Wire into routes + session flag + lifespan

**Files:** `app/chat/routes.py`, `app/sessions/routes.py`, `app/schemas/__init__.py`, `app/main.py`, and their tests.

**Interfaces:** consumes `ChatTurnRegistry`, `TurnInProgress`, `build_turn_registry` (Task 1). Produces `get_chat_turns(request) -> ChatTurnRegistry`; `GET /sessions/{id}/stream`; `SessionOut.activeTurn`.

- [ ] **Step 1: DI seam + refactor `POST /chat` + add `GET /stream`**

In `backend/app/chat/routes.py`:
- Add the seam (mirror `get_ingest_jobs`):
  ```python
  from fastapi import Request
  from app.chat.turns import ChatTurnRegistry, TurnInProgress

  def get_chat_turns(request: Request) -> ChatTurnRegistry:
      return request.app.state.chat_turns

  ChatTurnsDep = Annotated[ChatTurnRegistry, Depends(get_chat_turns)]
  ```
- Delete the local `_build_registry` (now `build_turn_registry` in `turns.py`).
- Refactor `chat(...)`: keep the ownership check and the `model_content`/`attachment_ids` computation (lines ~79-105). Replace the `ctx`/`gen()`/`run_turn` block with spawn + observe. The route no longer needs `client`/`embedder`/`model` params (the registry holds them) — replace them with `chat_turns: ChatTurnsDep`:
  ```python
  @router.post("/{session_id}/chat")
  def chat(session_id, body, user, db, chat_turns: ChatTurnsDep):
      _owned(db, user.id, session_id)
      # ... compute model_content + attachment_ids exactly as today ...
      try:
          chat_turns.spawn(
              session_id, user_id=user.id, message=body.message,
              model_content=model_content, attachment_ids=attachment_ids,
          )
      except TurnInProgress:
          raise ApiError(409, "turn_in_progress", "This chat already has a reply in progress")

      async def _stream():
          async for event in chat_turns.observe(session_id, from_index=0):
              yield sse(event)

      return StreamingResponse(_stream(), media_type="text/event-stream")
  ```
- Add the resume endpoint:
  ```python
  @router.get("/{session_id}/stream")
  def stream(session_id: str, user: CurrentUser, db: DbSession, chat_turns: ChatTurnsDep):
      _owned(db, user.id, session_id)

      async def _stream():
          if chat_turns.has_active(session_id):
              async for event in chat_turns.observe(session_id, from_index=0):
                  yield sse(event)

      return StreamingResponse(_stream(), media_type="text/event-stream")
  ```

- [ ] **Step 2: `activeTurn` on the session list**

- In `backend/app/schemas/__init__.py`, add to `SessionOut` (CamelModel → serializes as `activeTurn`):
  ```python
      active_turn: bool = False
  ```
- In `backend/app/sessions/routes.py::list_sessions`, add the `ChatTurnsDep` param (import `get_chat_turns`/`ChatTurnsDep` from `app.chat.routes`) and stamp the flag on each ORM row before returning:
  ```python
  def list_sessions(user, db, chat_turns: ChatTurnsDep):
      sessions = repo_or_query(...)          # unchanged fetch
      active = chat_turns.active_session_ids()
      for s in sessions:
          s.active_turn = s.id in active      # transient attr; SessionOut reads it
      return sessions
  ```

- [ ] **Step 3: lifespan — build the registry + shutdown**

In `backend/app/main.py` `lifespan`, alongside the `ingest_jobs` construction, add:
```python
    from app.chat.client import get_model_client
    from app.chat.turns import ChatTurnRegistry

    app.state.chat_turns = ChatTurnRegistry(
        model=get_model_client(),
        client=get_client(),
        embedder=get_embedder(),
        session_factory=get_session_factory(),
        max_concurrent=settings.max_concurrent_chat_turns,
    )
```
And after `yield` (next to the ingest shutdown):
```python
    await app.state.chat_turns.shutdown()
```

- [ ] **Step 4: Update chat/session route tests**

`test_chat_route.py` (and any session-list test) drives the chat route via `TestClient` with dependency overrides. Because the turn now runs through the registry (which builds its own `bg_db`/mcp/registry), override `get_chat_turns` in the test to a registry wired with the test's fake model + in-memory client/embedder + test `session_factory` (mirror how `test_kb.py` overrides `get_ingest_jobs`). Assert: `POST /chat` streams `token`/`done` and persists; a second `POST` while active → 409; `GET /stream` on a live turn replays, on an idle room returns an empty stream; `list_sessions` includes `activeTurn`.

- [ ] **Step 5: Run affected suites + import smoke**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_chat_route.py tests/test_chat_turns.py tests/test_sessions.py -q`
Then: `env -u VIRTUAL_ENV uv run python -c "import app.main; print('ok')"`
Expected: green; `ok`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/chat/routes.py backend/app/sessions/routes.py backend/app/schemas/__init__.py backend/app/main.py backend/tests/
git commit -m "feat(chat): run turns via the detached registry; add GET /stream resume + activeTurn flag

POST /chat spawns a detached turn and observes it (409 on a concurrent send);
new GET /sessions/{id}/stream replays+tails a live turn for resume; SessionOut
gains activeTurn from the registry. lifespan builds ChatTurnRegistry on
app.state and shuts it down."
```

---

### Task 3: Frontend — resume + shared consumer + sidebar + poll + send-lock

**Files:** `frontend/src/hooks/useStreamChat.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/queries.ts`, `frontend/src/types/chat.ts`, the chat route (`_auth.chat.$sessionId.tsx`), the sidebar, the composer.

- [ ] **Step 1: `activeTurn` on the session type + `streamResume` api**

- `frontend/src/types/chat.ts`: add `activeTurn: boolean;` to the session interface (near `title`/`updatedAt`).
- `frontend/src/lib/api.ts`: add `streamResume(sessionId)` — a GET SSE mirroring `streamChat` but `GET /api/sessions/${sessionId}/stream` (no body), yielding parsed `StreamEvent`s. Reuse the existing SSE-parse helper `streamChat` uses.

- [ ] **Step 2: Shared consumer + `resume()` in `useStreamChat`**

Refactor the `for await … switch(event.type)` body (lines ~54-105) into a shared `consumeStream(events: AsyncIterable<StreamEvent>)` that runs the same switch and the `finally` (setIsStreaming(false) + invalidate). Then:
- `sendMessage(...)` → `reset(); setIsStreaming(true); await consumeStream(streamChat(sessionId, message, attachments));`
- add `resume()` → if `isStreaming` return (guard double-consume); else `reset(); setIsStreaming(true); await consumeStream(streamResume(sessionId));`
- return `resume` alongside `sendMessage`.

- [ ] **Step 3: Resume-on-entry in the chat route**

In `frontend/src/routes/_auth.chat.$sessionId.tsx`, read the session's `activeTurn` (from the `useSessions` cache or a per-session fetch) and, on mount / session change, if it's set and not already streaming, call `resume()`. (A `useEffect` keyed on `sessionId` + the flag.)

- [ ] **Step 4: Poll the session list + sidebar dot + send-lock**

- `frontend/src/lib/queries.ts::useSessions`: add `refetchInterval` polling every ~2500ms while any session has `activeTurn` (mirror the KB `useKnowledgeBaseFiles` poll at `queries.ts:74`).
- Sidebar (`Sidebar.tsx` / `SidebarSection.tsx`): render a small "generating" dot/spinner on rows where `session.activeTurn`.
- Composer: disable the send control when the current room's `activeTurn` is set (or `isStreaming`); surface a 409 gracefully (it means a turn is already running — treat like "already streaming").

- [ ] **Step 5: Typecheck + build**

Run (from `frontend/`): `npm run build`
Expected: clean typecheck + build.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(chat-ui): live-resume turns + per-room generating indicator

useStreamChat factors a shared consumer used by sendMessage and a new resume()
that reattaches to GET /stream; the chat route resumes on entry when the room's
activeTurn is set. useSessions polls while any turn is active; the sidebar shows
a per-room generating dot; send is locked while a room has a live turn."
```

---

### Task 4: Full-suite verification

- [ ] **Step 1:** `env -u VIRTUAL_ENV uv run pytest -q` (from `backend/`) — expect the 348 baseline + new chat-turn tests, 0 failures.
- [ ] **Step 2:** `npm run build` (from `frontend/`) — clean.
- [ ] **Step 3 (manual):** run FE+BE; start a turn in room A, switch to room B, switch back → watch A keep typing; confirm the sidebar dot on A; confirm send is disabled in A while it runs.
- [ ] **Step 4:** `git log --oneline main..HEAD` — spec + three task commits on `feat/live-chat-turns`.

---

## Self-Review

**Spec coverage:** registry+replay (§4) → Task 1; POST/GET/flag routes (§5) → Task 2; lifespan (§4.2) → Task 2 Step 3; frontend resume+poll+sidebar+send-lock (§6) → Task 3; money+replay+has_active+double-spawn tests (§8) → Task 1 tests + Task 2 route tests. ✅

**Placeholder scan:** complete code for Task 1 (the async-subtle part) and the route/lifespan blocks; Task 3's UI wiring gives exact files + the code blocks + explicit "mirror `queries.ts:74` / `get_ingest_jobs`" references (the same-shape precedents exist in-repo). No TBD/TODO.

**Type consistency:** `ChatTurnRegistry(*, model, client, embedder, session_factory, max_concurrent)`, `spawn(session_id, *, user_id, message, model_content, attachment_ids)`, `observe(session_id, from_index)`, `has_active`, `active_session_ids`, `TurnInProgress`, `build_turn_registry`, `get_chat_turns`, `SessionOut.active_turn` / `activeTurn`, `resume()` used consistently across tasks. `settings.max_concurrent_chat_turns` produced in Task 1, consumed in Task 2. ✅
