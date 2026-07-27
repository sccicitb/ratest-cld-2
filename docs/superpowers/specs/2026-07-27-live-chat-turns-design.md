# Live chat turns — decouple from the connection + resume + cross-chat progress — design

- **Date:** 2026-07-27
- **Status:** Draft (awaiting review)
- **Scope:** Chat turn lifecycle + a new resume endpoint + a per-room activity flag. QoL "thread B".
- **Related:** ingest decouple + reaper (`2026-07-23-ingest-decouple-reaper-design.md`) — this is that pattern applied to chat, plus multi-observer replay. Token streaming (`2026-07-27-stream-answer-tokens`) already shipped.

## 1. Problem

A chat turn runs `run_turn` **inline inside the SSE response** (`app/chat/routes.py` `gen()`), and the assistant message persists **only at the end** (`loop.py:300-316`). So navigating to another chat mid-turn cancels the SSE → the turn dies → **the answer is lost** (the ingest-orphan class). There is also **no signal of which rooms have a turn in flight** — `useSessions()` doesn't poll and the sidebar has nothing to show.

## 2. Decision

Run each chat turn as a **detached, app-owned `asyncio` task** in a registry (mirroring `ingest_jobs`), with a per-turn **append-only replay log** and **multi-observer** tailing. The turn runs to completion and persists regardless of the connection. A new `GET /sessions/{id}/stream` lets a client that navigates back **live-resume** (replay what it missed, then tail). The session list gains an `activeTurn` flag so the sidebar shows a per-room "generating" dot and the client knows to resume; the list polls while any turn is active (the KB "poll-while-indexing" pattern).

**Decided forks:** live-resume (not refetch-only); cross-chat signal = **flag + poll** (not push); one turn per room = **reject the second send (409)**, not queue.

## 3. Goals / Non-goals

**Goals**
- A chat turn survives client disconnect / navigation: it runs to completion and persists the assistant message.
- Navigating back into a room with a live turn **resumes the live stream** (replay + tail).
- The sidebar shows which rooms are currently generating; sending is blocked in a room that already has a live turn.
- Same SSE event shapes (`step`/`token`/`reasoning`/`artifact`/`done`/`error`) — the existing consumer works unchanged.

**Non-goals**
- No global activity push channel (poll suffices; fast-follow).
- No queueing of concurrent sends to one room (reject with 409).
- No change to `run_turn`'s tool loop, persistence content, or retrieval.
- No persistence of the replay log (ephemeral, turn-lifetime).

## 4. Architecture — chat-turn registry with replay

New module `app/chat/turns.py`. One `ChatTurnRegistry` built at startup on `app.state.chat_turns`, holding the app-singleton deps (model client, Qdrant, embedder, session_factory) + a concurrency semaphore.

```python
class ChatTurnJob:
    session_id: str
    task: asyncio.Task
    events: list[dict]    # append-only replay log of every event emitted so far
    done: bool
    # + a broadcast notify (an asyncio.Event swapped on each append) so multiple
    #   observers can tail; each observer keeps its own cursor into `events`.

class ChatTurnRegistry:
    def spawn(self, session_id: str, *, params) -> ChatTurnJob: ...
    async def observe(self, session_id: str, from_index: int = 0) -> AsyncIterator[dict]: ...
    def has_active(self, session_id: str) -> bool: ...
    def active_session_ids(self) -> set[str]: ...
    async def shutdown(self) -> None: ...
```

### 4.1 Lifecycle (reuses the ingest lessons)
- **`spawn`** — one job per `session_id`; if one already exists, raise (the route maps it to 409). `asyncio.create_task(self._run(job))`, store keyed by `session_id`, **identity-checked** done-callback eviction (`self._jobs.pop(sid) if self._jobs.get(sid) is this`).
- **`_run`** — `async with self._sem:` → open `bg_db = session_factory()` → resolve MCP tools + build the registry (the work currently in the route's `gen()`) → `async for ev in run_turn(...): job.publish(ev)` → close `bg_db` in `finally`. `run_turn` already guards its own exceptions and yields a final `error` event; `_run` marks `done` in `finally`.
- **`publish(ev)`** — append to `job.events`, wake observers.
- **`observe(session_id, from_index)`** — capture the job; replay `events[from_index:]`, then tail (await the notify, drain new events) until `done`. Multi-observer: each call keeps its own cursor. Holds its own job ref so eviction mid-drain is safe.
- **Eviction:** on task completion the job is evicted from the dict a beat later; a client reconnecting after that finds no active turn and loads the **persisted** message instead (source of truth). No grace buffer needed.

### 4.2 Deps captured at startup
The detached task needs the model client / Qdrant / embedder / session_factory. Build the registry in `lifespan` with `get_model_client()`, `get_client()`, `get_embedder()`, `get_session_factory()` (like `ingest_jobs`), stored on `app.state.chat_turns`; `await shutdown()` on stop.

## 5. Routes

- **`POST /sessions/{id}/chat`** (refactor): compute `model_content`/attachment prep as today, then `chat_turns.spawn(session_id, …)`. If a turn is already active for the room → **409** (`ApiError("turn_in_progress")`). Return the SSE observing the job **from index 0**.
- **`GET /sessions/{id}/stream`** (new; auth + `_owned` ownership): if `chat_turns.has_active(id)`, return SSE `observe(id, from_index=0)` (replay + tail). If not, return an empty stream (client falls back to persisted messages).
- **`GET /sessions`** (`list_sessions`): add `activeTurn: bool` to `SessionOut`, populated from `chat_turns.active_session_ids()` (one registry read, no per-row query).

## 6. Frontend

- **`useStreamChat`** — extract the event-processing loop into a shared `consumeStream(events)`; `sendMessage()` (POST) uses it, and a new **`resume(sessionId)`** opens `GET /sessions/{id}/stream` and feeds it through the same consumer. `resume` first resets local state so the replayed events rebuild `streamedContent`/`streamedReasoning`/`steps` exactly. Guard against double-consume (don't resume while already streaming).
- **Chat route (`_auth.chat.$sessionId.tsx`)** — on mount / session change, if the session's `activeTurn` is set, call `resume(sessionId)`.
- **`useSessions`** — add `refetchInterval` polling while any `activeTurn` is true (mirror `queries.ts:74` KB poll).
- **Sidebar** — a per-room "generating" dot/spinner from `session.activeTurn`.
- **Composer** — disable send in a room whose `activeTurn` is set (or while streaming/resuming); a 409 from POST is surfaced gracefully.
- **`types/chat.ts`** — add `activeTurn: boolean` to the session type.

## 7. Data flow

```
POST /chat ─▶ spawn(session_id) ──▶ detached task: run_turn → publish(ev)→ job.events[]  (survives disconnect, persists at end)
             └▶ SSE observe(from=0) ──▶ live tokens/steps to the sender

navigate away → sender SSE cancels; task runs on.
navigate back → activeTurn flag set → GET /stream → observe(from=0): replay events[] (catch up) + tail live → done.
sidebar: useSessions poll → activeTurn dots.
```

## 8. Testing

- **Registry (`tests/test_chat_turns.py`):**
  - Money test — spawn a turn, observe + cancel the observer mid-stream, assert the **task still completes** and the assistant `Message` persists.
  - Replay/fan-out — spawn; a first observer consumes some events; a **second** `observe(from_index=0)` replays the full log then tails to `done` (proves multi-observer resume).
  - `has_active` / `active_session_ids` reflect a live vs finished turn.
  - Second `spawn` for the same session raises (→ 409).
- **Routes:** `POST /chat` streams + persists; a concurrent `POST` to the same room → 409; `GET /stream` on a live turn replays+tails, on an idle room returns empty; `list_sessions` includes `activeTurn`.
- **Frontend:** typecheck + build; controller/user click-through — start a turn, switch rooms, switch back → watch it keep typing; sidebar dot on the busy room.
- Full backend suite stays green.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Detached task GC'd | Registry dict holds the ref; identity-checked eviction (ingest lesson). |
| Replay log unbounded | One turn's worth of events, ephemeral, evicted on completion. |
| Double-consume on the client (POST + resume) | `resume` no-ops if already streaming; route resumes only when not the sender. |
| Cross-thread/session in the task | `_run` uses its own `bg_db`; publish/observe are in-loop; no shared session. |
| Shutdown mid-turn | `registry.shutdown()` cancels → `run_turn`'s guard + persistence; the turn's partial answer isn't persisted (documented; a follow-up could persist partials). |

## 10. Follow-ups (not built here)

- Global activity **push** (SSE/WS) if the 2.5s poll granularity feels laggy.
- Queue (not reject) concurrent sends to a room.
- Persist a partial answer if a turn is cancelled by shutdown.
