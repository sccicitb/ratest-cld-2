# Backend RAG Core — Staged Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement each stage task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This is a staging roadmap.** It sequences the whole backend into 6 stages (+ phase-2). **Stage 1 is written in full bite-sized TDD detail** (it's the immediate next slice). Stages 2–6 carry actionable task-level detail — files, interfaces with exact signatures, the ordered tasks, key code sketches, and a definition of done. **Expand each later stage into full bite-sized steps (re-run writing-plans) when you start it**, since details (e.g. the model's exact tool-call shape) firm up once the prior stage lands.

**Goal:** Implement the self-hosted RAG backend defined in [`docs/BACKEND_SPEC.md`](../../BACKEND_SPEC.md) (status: LOCKED) so the existing React frontend works end-to-end against it.

**Architecture:** FastAPI app; SQLAlchemy/SQLite app DB for relational data; Qdrant for chunk vectors; FastEmbed (BGE-M3) in-process for embeddings + rerank; any OpenAI-compatible endpoint for the chat model, driven by a hand-written tool-use loop that streams SSE `StreamEvent`s. Retrieval is an agentic tool (`search_knowledge_base`) with scope injected server-side.

**Tech Stack:** Python ≥3.10 · uv · FastAPI · SQLAlchemy 2 + Alembic · Qdrant (`qdrant-client`) · FastEmbed · OpenAI SDK (pointed at `MODEL_BASE_URL`) · PyMuPDF/Surya (extraction).

## Global Constraints

Every task implicitly includes these (copied from the spec):

- **Python ≥ 3.10**, dependency management via **uv**; heavy deps live in extras (`rag`, `llm`, `ingest`, `mcp`).
- All routes under **`/api`**. Auth via `Authorization: Bearer` on every route **except** `POST /auth/login` and `POST /auth/refresh` (cookie-based).
- **JSON out is camelCase** (`CamelModel` alias generator). **Error bodies are top-level `{message, code}`** (Stage 1 makes this true).
- **Timestamps: ISO-8601 UTC** (`...Z`) — Stage 1 makes naive SQLite datetimes serialize correctly.
- **SSE** endpoints return `text/event-stream`: one JSON event per `data:` line, blank line between events, stream closed after the terminal event. Disable proxy buffering; emit errors as a final event, never an HTTP error mid-stream.
- **Chat model is model-agnostic**: OpenAI-compatible `/v1/chat/completions` (`messages` + `tools`; read `tool_calls`; reply `role:"tool"` + `tool_call_id`). No vendor SDK in the loop logic. Default `MODEL_BASE_URL=https://llama.sccic.org/v1`.
- **Embeddings pinned**: BGE-M3, **dense 1024-dim (Cosine) + sparse**, in-process FastEmbed. Same model for index + query. **Never swap** (re-index otherwise).
- **One Qdrant collection** `kb_chunks`; scope fields **denormalized onto every point's payload** (`user_id, scope, session_id, status`); payload-indexed.
- **App DB**: SQLAlchemy + Alembic + repository pattern; `tags` as JSON (not native array); UUIDs as text; SQLite WAL.
- **Scope filter (the security boundary):** `user_id == caller` AND `status == "ready"` AND (`scope == "kb"` OR (`scope == "session"` AND `session_id == current_session`)). Injected server-side; the model never supplies it.
- `INLINE_TOKEN_BUDGET` default **6000**; `MAX_TOOL_ITERATIONS` **6**; `MAX_PARALLEL_TOOLS` **2** (all in `app/config.py`).
- Auth refresh cookie: httpOnly, `SameSite=Lax`, `Secure` configurable (same-origin via reverse proxy / Vite `/api` proxy).

## Stage map

| Stage | Delivers (independently testable) | Size | Depends on | Spec |
|---|---|---|---|---|
| **0** | Foundation: models/migrations, auth, sessions | — | — | §4,§5,§9.1 ✅ **DONE** |
| **1** | Cross-cutting fixes: error envelope + UTC timestamps | S | 0 | §2 |
| **2** | Vector infra: FastEmbed embedder + Qdrant collection/upsert/search | M | 1 | §8.1,§8.5,§9.2 |
| **3** | Ingestion pipeline + KB endpoints (upload→indexed→listed) | L | 2 | §8.1,§8.3 |
| **4** | Retrieval + tool registry + `search_knowledge_base` | M | 3 | §7,§8.5 |
| **5** | Chat SSE tool-use loop (the RAG experience) | L | 4 | §7 |
| **6** | Attachments + session files + promote | M | 5 | §6.1,§8.4 |
| **P2** | Sandbox (§13), MCP/satudata (§12.2), deployment | L | 5 | §12.2,§13 |

After Stage 5 the product is a working RAG chat (KB + agentic retrieval + streaming). Stage 6 adds chat attachments. P2 is optional.

---

## Stage 1 — Cross-cutting fixes (S)

**Goal:** Error responses are top-level `{message, code}` and timestamps serialize as ISO-8601 UTC. Both are wire-contract requirements (§2) the frontend's `ApiError` and relative-time display depend on.

**Files:**
- Create: `backend/app/errors.py` — `ApiError` exception + handlers.
- Modify: `backend/app/main.py` — register handlers.
- Modify: `backend/app/schemas/__init__.py` — UTC datetime serializer on `CamelModel`.
- Modify: `backend/app/auth/deps.py`, `backend/app/auth/routes.py`, `backend/app/sessions/routes.py` — raise `ApiError` instead of `HTTPException(detail={...})`.
- Test: `backend/tests/test_errors.py`.

**Interfaces produced:**
- `class ApiError(Exception)` with `__init__(self, status: int, code: str, message: str)`.
- Handler turns any `ApiError` into `JSONResponse(status, {"message", "code"})`.
- A FastAPI `HTTPException`/validation handler also emits `{message, code}` (so 422s conform).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_errors.py
def test_404_uses_message_code_envelope(client, auth_headers):
    r = client.get("/api/sessions/does-not-exist", headers=auth_headers)
    assert r.status_code == 404
    body = r.json()
    assert body == {"message": "Session not found", "code": "not_found"}
    assert "detail" not in body


def test_401_envelope(client):
    r = client.get("/api/sessions")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_validation_error_envelope(client, auth_headers):
    # PATCH with a wrong-typed title triggers a 422 from FastAPI.
    r = client.patch("/api/sessions/x", headers=auth_headers, json={"title": 123})
    assert r.status_code in (404, 422)
    assert "message" in r.json() and "code" in r.json()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_errors.py -v`
Expected: FAIL — body is `{"detail": {...}}`, missing top-level `message`/`code`.

- [ ] **Step 3: Create `app/errors.py`**

```python
# backend/app/errors.py
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.status, {"message": exc.message, "code": exc.code})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "message" in detail:
            body = {"message": detail["message"], "code": detail.get("code", "error")}
        else:
            body = {"message": str(detail), "code": "error"}
        return JSONResponse(exc.status_code, body)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(422, {"message": "Validation error", "code": "validation_error"})
```

- [ ] **Step 4: Register handlers in `app/main.py`** (after `app = FastAPI(...)`)

```python
from app.errors import register_error_handlers  # noqa: E402
register_error_handlers(app)
```

- [ ] **Step 5: Replace `HTTPException` raises with `ApiError`**

In `app/sessions/routes.py` `_owned`:
```python
from app.errors import ApiError
...
    if not s or s.user_id != user_id:
        raise ApiError(404, "not_found", "Session not found")
```
In `app/auth/deps.py` `_unauthorized`:
```python
from app.errors import ApiError
def _unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "Unauthorized")
```
In `app/auth/routes.py`, swap the two `HTTPException(401, {...})` for `ApiError(401, "invalid_credentials", "Invalid credentials")` and `ApiError(401, "invalid_refresh", "Invalid refresh")`.

- [ ] **Step 6: Add the UTC datetime serializer to `CamelModel`** (`app/schemas/__init__.py`)

```python
from datetime import datetime, timezone
from pydantic import field_serializer

class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _utc_datetimes(self, v):  # noqa: ANN001
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return v
```

- [ ] **Step 7: Write the timestamp test**

```python
# add to tests/test_errors.py (or tests/test_sessions.py)
def test_timestamps_are_utc_z(client, auth_headers):
    sid = client.post("/api/sessions", headers=auth_headers).json()
    assert sid["createdAt"].endswith("Z")
```

- [ ] **Step 8: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS (all prior + new).

- [ ] **Step 9: Commit**

```bash
git add backend/app/errors.py backend/app/main.py backend/app/schemas/__init__.py \
        backend/app/auth backend/app/sessions backend/tests/test_errors.py
git commit -m "backend: error envelope {message,code} + UTC timestamp serialization"
```

**Definition of done:** Every error body is `{message, code}`; all timestamps end in `Z`; suite green.

---

## Stage 2 — Vector infra (M)

**Goal:** A FastEmbed embedder and a Qdrant gateway that can bootstrap the collection, upsert chunks with denormalized scope payload, run a scoped hybrid search, and delete/update by file/session. Pure infra — no HTTP.

**Setup:** `cd backend && uv sync --extra rag`. Tests use Qdrant **local mode** (`QdrantClient(":memory:")`) — no server needed. (`docker compose up -d qdrant` for manual runs.)

**Files:**
- Create: `backend/app/rag/embedder.py`
- Create: `backend/app/rag/vectors.py`
- Test: `backend/tests/test_vectors.py`

**Interfaces produced:**
```python
# embedder.py
class Embedder:
    def embed_passages(self, texts: list[str]) -> list[Embedding]   # {dense: list[float], sparse: SparseVector}
    def embed_query(self, text: str) -> Embedding
def get_embedder() -> Embedder   # process-singleton (loads BGE-M3 once)

# vectors.py
COLLECTION = "kb_chunks"
class Chunk(TypedDict): content:str; file_id:str; chunk_idx:int; tags:list[str]; user_id:str; scope:str; session_id:str|None; status:str
def ensure_collection(client) -> None
def upsert_chunks(client, embedder, chunks: list[Chunk]) -> None
def search(client, embedder, *, query:str, user_id:str, session_id:str|None, k:int=5) -> list[Chunk]
def delete_by_file(client, file_id:str) -> None
def delete_by_session(client, session_id:str) -> None
def update_file_payload(client, file_id:str, patch:dict) -> None   # promote/reindex
def get_client() -> QdrantClient   # from settings.qdrant_url
```

**Key code sketch — `ensure_collection` (named vectors per §9.2):**
```python
from qdrant_client import models as qm
client.create_collection(
    COLLECTION,
    vectors_config={"dense": qm.VectorParams(size=1024, distance=qm.Distance.COSINE)},
    sparse_vectors_config={"sparse": qm.SparseVectorParams()},
)
for field in ("user_id", "scope", "session_id", "status"):
    client.create_payload_index(COLLECTION, field_name=field,
        field_schema=qm.PayloadSchemaType.KEYWORD)
```

**Key code sketch — scoped hybrid `search` (§7):**
```python
emb = embedder.embed_query(query)
scope_filter = qm.Filter(must=[
    qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id)),
    qm.FieldCondition(key="status", match=qm.MatchValue(value="ready")),
    qm.Filter(should=[
        qm.FieldCondition(key="scope", match=qm.MatchValue(value="kb")),
        qm.Filter(must=[
            qm.FieldCondition(key="scope", match=qm.MatchValue(value="session")),
            qm.FieldCondition(key="session_id", match=qm.MatchValue(value=session_id)),
        ]),
    ]),
])
res = client.query_points(COLLECTION, prefetch=[
        qm.Prefetch(query=emb.dense, using="dense", limit=50, filter=scope_filter),
        qm.Prefetch(query=qm.SparseVector(**emb.sparse), using="sparse", limit=50, filter=scope_filter),
    ], query=qm.FusionQuery(fusion=qm.Fusion.RRF), limit=k, with_payload=True)
return [p.payload for p in res.points]
```

**Tasks (each: write test → run-fail → implement → run-pass → commit):**
- [ ] **Task 2.1 — Embedder.** Test: `embed_query("hello")` returns dense len 1024 + non-empty sparse; `embed_passages` batches. Implement the FastEmbed BGE-M3 wrapper + singleton.
- [ ] **Task 2.2 — Collection bootstrap.** Test: `ensure_collection` is idempotent (call twice; collection exists with `dense`+`sparse`). Implement.
- [ ] **Task 2.3 — Upsert + scoped search round-trip.** Test: upsert 3 chunks (2 `kb`, 1 `session` for session S) for user U; `search(query, user_id=U, session_id=S)` returns matches; `search(... session_id="other")` excludes the session chunk; `search(user_id="other")` returns nothing. Implement upsert + search.
- [ ] **Task 2.4 — Delete + payload update.** Test: `delete_by_file` removes a file's points; `update_file_payload(file_id, {"scope":"kb","session_id":None})` flips them (verify via search scope). Implement.

**Definition of done:** `uv run pytest tests/test_vectors.py` green; scope isolation proven (cross-user and cross-session leakage tests pass).

---

## Stage 3 — Ingestion pipeline + KB endpoints (L)

**Goal:** Upload a document to the KB, watch it index over SSE, and see it listed/searchable. Reindex/tags/delete work. This is the write side end-to-end.

**Setup:** `uv sync --extra ingest` (PyMuPDF, python-docx). Surya optional/deferred — stub the OCR branch behind a flag initially (see Task 3.2).

**Files:**
- Create: `backend/app/storage.py` — blob save/load (`save(file) -> storage_key`, `open(storage_key)`).
- Create: `backend/app/rag/extract.py` — `extract_text(path, mime) -> str` with PyMuPDF + density check + OCR hook + docx/txt/csv/json.
- Create: `backend/app/rag/chunk.py` — `chunk(text) -> list[str]`.
- Create: `backend/app/rag/ingest.py` — `async def ingest(file_id) -> AsyncIterator[ProgressEvent]` (extract→chunk→embed→upsert→status).
- Create: `backend/app/kb/repo.py` — `KBFileRepo` (list w/ filters, create, get, set_status, update_tags, delete).
- Create: `backend/app/kb/routes.py` — list, upload (SSE), reindex, tags, delete.
- Create: `backend/app/sse.py` — `sse(event_dict) -> str` + `EventSourceResponse`-style helper.
- Modify: `backend/app/main.py` — include `kb_router` at `/api/knowledge-base`.
- Test: `backend/tests/test_kb.py`, `backend/tests/test_extract.py`, `backend/tests/test_chunk.py`.

**Interfaces produced:**
```python
# sse.py
def sse(data: dict) -> bytes                       # b"data: {json}\n\n"
# storage.py
def save_upload(file: UploadFile) -> tuple[str, int]   # (storage_key, size)
def open_blob(storage_key: str) -> BinaryIO
# extract.py
SUPPORTED_KB_TYPES = {".pdf", ".md", ".txt", ".docx", ".doc", ".csv", ".json"}
def extract_text(storage_key: str, filename: str) -> str
# chunk.py
def chunk(text: str, target_tokens: int = 800, overlap: float = 0.12) -> list[str]
# ingest.py — yields {"type":"chunk_progress","fileName","progress","chunkCount","total"}; finalizes status
async def ingest(db, file_id: str) -> AsyncIterator[dict]
```

**SSE upload shape (§8.3):** response streams `chunk_progress` events then `{"type":"file_resolved","file": <KnowledgeBaseFile>}` then `{"type":"done"}`.

**Tasks:**
- [ ] **Task 3.1 — `sse()` + storage.** Tests: `sse({"a":1}) == b"data: {\"a\":1}\n\n"`; `save_upload` returns a key + size and `open_blob` round-trips bytes.
- [ ] **Task 3.2 — Extraction.** Tests: a tiny text-layer PDF (fixture) → non-empty text via PyMuPDF; a `.txt`/`.md`/`.csv`/`.json` → text; unknown ext → raises. Density check: a near-empty-text PDF routes to `_ocr()` (initially a stub raising `NotImplementedError("OCR")` behind `settings` flag — real Surya is a follow-up task). `.docx` via python-docx.
- [ ] **Task 3.3 — Chunking.** Test: a 5000-char text → multiple chunks, each ≤ target, consecutive chunks overlap.
- [ ] **Task 3.4 — Ingest orchestration.** Test (Qdrant `:memory:`): `ingest(db, file_id)` yields ≥1 `chunk_progress`, upserts points to Qdrant, sets `kb_files.status="ready"` + `chunk_count>0`. On extract failure → `status="error"`.
- [ ] **Task 3.5 — KB repo + list/filters.** Tests: list returns only `scope="kb"` sorted by `upload_date desc`; `search`/`status`/`tag` filters apply (AND).
- [ ] **Task 3.6 — Upload endpoint (SSE).** Test: `POST /api/knowledge-base/upload` (multipart, a text fixture) returns `text/event-stream`; parse events → ends with `file_resolved` (status `ready`); the file then appears in `GET /knowledge-base`. Reject `.png` with 415.
- [ ] **Task 3.7 — reindex / tags / delete.** Tests: `PATCH /:id/tags` lowercases+dedupes; `DELETE /:id` removes the row **and** its Qdrant points (`delete_by_file`); `POST /:id/reindex` sets `indexing` then re-runs (background) to `ready`.
- [ ] **Task 3.8 — Surya OCR branch (optional, can defer).** Replace the OCR stub with Surya; gated by a settings flag; test with a scanned-PDF fixture or skip if weights absent.

**Definition of done:** Upload→SSE→ready→listed→deletable; suite green; a doc's text is retrievable via Stage 2 `search`.

---

## Stage 4 — Retrieval + tool registry (M)

**Goal:** A `search_knowledge_base` tool that takes a query, runs the scoped hybrid search (Stage 2), optionally reranks, and returns top-k chunks — with scope injected from server-side context, never the model.

**Setup:** rerank uses FastEmbed `TextCrossEncoder` with `BAAI/bge-reranker-v2-m3` via `add_custom_model` (behind `settings.rerank_enabled`, default False).

**Files:**
- Create: `backend/app/rag/retrieve.py` — `retrieve(query, *, user_id, session_id, k) -> list[Chunk]` (search → optional rerank).
- Create: `backend/app/rag/rerank.py` — `rerank(query, chunks, k) -> list[Chunk]` (flagged).
- Create: `backend/app/tools/registry.py` — `Tool` protocol + `ToolRegistry`.
- Create: `backend/app/tools/context.py` — `ToolContext` (carries `user_id`, `session_id`, `db`, progress callback).
- Create: `backend/app/tools/builtin/search_kb.py` — the native tool.
- Test: `backend/tests/test_retrieve.py`, `backend/tests/test_tools.py`.

**Interfaces produced:**
```python
# tools/registry.py
@dataclass
class ToolContext: user_id:str; session_id:str|None; db:Session
class Tool(Protocol):
    name:str
    def schema(self) -> dict        # OpenAI function schema
    async def execute(self, args:dict, ctx:ToolContext) -> str
class ToolRegistry:
    def register(self, tool:Tool) -> None
    def schemas(self) -> list[dict]
    async def execute(self, name:str, args:dict, ctx:ToolContext) -> str
# tools/builtin/search_kb.py
class SearchKnowledgeBase(Tool):  # name="search_knowledge_base"
    # schema = the §7 function def; execute() calls retrieve() with ctx scope, returns formatted chunks
```

**Tasks:**
- [ ] **Task 4.1 — Retrieve.** Test (`:memory:` Qdrant seeded): `retrieve("...", user_id=U, session_id=S)` returns chunks; respects scope (reuse Stage 2 isolation). With `rerank_enabled=False`, returns recall order.
- [ ] **Task 4.2 — Tool registry.** Tests: `register` + `schemas()` returns the OpenAI function list; `execute("unknown", ...)` raises a tool error; `execute` dispatches by name.
- [ ] **Task 4.3 — search_kb tool.** Test: `tool.execute({"query":"x"}, ctx)` returns a string containing the top chunk's content; **scope comes from `ctx`** — a tool call can't reach another session's chunks even if args try (there's no session arg). `schema()` matches §7 (`query` required, optional `tags`).
- [ ] **Task 4.4 — Rerank (optional).** Test behind flag: with `rerank_enabled=True`, ordering changes vs recall-only on a crafted fixture; skip if model weights absent.

**Definition of done:** `search_knowledge_base.execute` returns scoped results; cross-session isolation test passes; registry dispatches.

---

## Stage 5 — Chat SSE tool-use loop (L) — the RAG experience

**Goal:** `POST /sessions/:id/chat` streams a real agentic answer: thinking → (0..n) tool calls → tokens → done, persisting both messages, using `search_knowledge_base` with server-injected scope.

**Setup:** `uv sync --extra llm`. Tests **mock the model client** (no live LLM) to assert the loop + event mapping deterministically.

**Files:**
- Create: `backend/app/chat/client.py` — `chat_stream(messages, tools) -> AsyncIterator[Delta]` over the OpenAI-compatible endpoint; injectable/mockable.
- Create: `backend/app/chat/events.py` — typed `StreamEvent` builders (`step`, `token`, `done`, `error`).
- Create: `backend/app/chat/loop.py` — `async def run_turn(db, session, user_msg, registry) -> AsyncIterator[dict]` (the manual loop).
- Create: `backend/app/chat/routes.py` — `POST /sessions/:id/chat` → `StreamingResponse`.
- Modify: `backend/app/main.py` — include `chat_router` at `/api/sessions`.
- Modify: `backend/app/tools/...` — build the registry with `SearchKnowledgeBase` for the turn.
- Test: `backend/tests/test_chat_loop.py`, `backend/tests/test_chat_route.py`.

**Interfaces produced:**
```python
# chat/client.py
class ModelClient(Protocol):
    def stream(self, messages:list[dict], tools:list[dict]) -> AsyncIterator["ModelChunk"]
# ModelChunk carries text delta and/or tool_calls (OpenAI shape)
def get_model_client() -> ModelClient   # real, points at MODEL_BASE_URL
# chat/loop.py
async def run_turn(*, db, session, message:str, registry, model:ModelClient,
                   ctx:ToolContext) -> AsyncIterator[dict]   # yields StreamEvent dicts
```

**Loop algorithm (locked, §7 "Loop implementation"):**
1. Persist user message; auto-title if `"New Chat"` (truncate ~40); build `messages` (history + turn). Emit `step thinking active`.
2. `model.stream(messages, registry.schemas())`.
3. If no tool_calls → emit `thinking complete`, `generating_response active`, stream deltas as `token`, then `complete`; break.
4. If tool_calls → per call emit `calling_tool active` (unique `id`, `toolName`, `toolArgs` with backend-injected `scope` string); `await registry.execute(name, args, ctx)`; emit `calling_tool complete`; append assistant tool-call msg + `role:"tool"` results; loop to 2. Cap at `MAX_TOOL_ITERATIONS` (force final answer), `MAX_PARALLEL_TOOLS` concurrency.
5. Persist assistant message; emit `done` with its id. **Stream text only on no-tool-call turns.**

**Tasks:**
- [ ] **Task 5.1 — Event builders.** Tests: `step("thinking","active")`/`token("hi")`/`done("m1")` produce the exact `StreamEvent` dicts from `src/types/chat.ts`.
- [ ] **Task 5.2 — Loop, zero tools (mocked model).** Mock model yields plain text. Test: `run_turn` emits `thinking active/complete`, `generating_response active`, ≥1 `token`, `generating_response complete`, `done`; persists user + assistant messages; auto-titles.
- [ ] **Task 5.3 — Loop, one tool call (mocked).** Mock model: first response = a `search_knowledge_base` tool_call, second = text. Test: emits a `calling_tool active`+`complete` pair with a unique `id` and `toolArgs.query`; the tool executes with scoped `ctx`; final tokens stream; `done`.
- [ ] **Task 5.4 — Iteration cap.** Mock model that always returns a tool call. Test: stops after `MAX_TOOL_ITERATIONS`, forces a final answer, emits `done` (no infinite loop).
- [ ] **Task 5.5 — Route (SSE).** Test (mocked model via dependency override): `POST /api/sessions/:id/chat` returns `text/event-stream`; parsing yields the event sequence ending in `done`; `GET /messages` then shows the persisted user+assistant pair. Unauth → 401; other-user session → 404.
- [ ] **Task 5.6 — Real model client.** Implement `get_model_client()` against `MODEL_BASE_URL` (OpenAI SDK, `stream=True`); manual `curl`/script smoke test against the live llama-server (not in CI). SSE plumbing: no-buffer headers, mid-stream error event, client-disconnect cancel.

**Definition of done:** Mocked loop tests green; live smoke shows a streamed answer that calls the KB tool; messages persisted; trivial message → 0 tool calls.

---

## Stage 6 — Attachments + session files + promote (M)

**Goal:** Attach files in chat — small ones inline, large ones session-ingested and retrievable in that chat — plus list session files and promote to KB. Wire session-delete Qdrant cleanup.

**Files:**
- Create: `backend/app/sessions/attachments.py` — `POST /sessions/:id/attachments` (multipart→SSE), token-count routing.
- Create: `backend/app/sessions/files.py` — `GET /sessions/:id/files`, `POST /sessions/:id/files/:fileId/promote`.
- Modify: `backend/app/chat/loop.py` — load inline attachment text into the turn's context.
- Modify: `backend/app/sessions/routes.py` — delete handler calls `delete_by_session` (Qdrant) before cascading rows (§8.2 ordering).
- Modify: `backend/app/rag/extract.py` — `count_tokens(text)` helper for routing.
- Test: `backend/tests/test_attachments.py`, `backend/tests/test_session_files.py`.

**Interfaces produced:**
```python
# attachments route → SSE: per file, chunk_progress (if ingest) then
#   {"type":"attachment_resolved","attachment": <Attachment with authoritative `ingested`>}
# routing: extract → count_tokens → <= INLINE_TOKEN_BUDGET ? inline : session-ingest
def route_by_tokens(text:str) -> Literal["inline","ingest"]
```

**Tasks:**
- [ ] **Task 6.1 — Token routing.** Test: short text → `"inline"`; large text → `"ingest"` (threshold `INLINE_TOKEN_BUDGET`).
- [ ] **Task 6.2 — Attachments upload (SSE).** Test: upload a small text → `attachment_resolved {ingested:false}`, no Qdrant points; upload a large text → `chunk_progress`+ `attachment_resolved {ingested:true}`, a `kb_files(scope=session)` row + Qdrant points scoped to the session.
- [ ] **Task 6.3 — Inline into context.** Test: an inline attachment's text appears in the `messages` the loop sends to the model (assert via mocked client capturing messages).
- [ ] **Task 6.4 — Session files + promote.** Tests: `GET /sessions/:id/files` returns only that session's `scope=session` files (not on `GET /knowledge-base`); `promote` flips scope→kb (row + Qdrant payload via `update_file_payload`), then it appears in `GET /knowledge-base` and not in session files.
- [ ] **Task 6.5 — Delete cleanup.** Test: deleting a session removes its session-file Qdrant points (`delete_by_session`) then cascades rows; a subsequent search finds nothing.

**Definition of done:** Attach small→inline (in context), large→session-ingest (retrievable in that chat only), promote→KB; session delete purges vectors; suite green.

---

## Phase 2 (separate plans — RAG core ships without these)

- **Sandbox `execute_code` (§13)** — Docker-per-conversation, IPython runner, network walls, lifecycle. Write its own plan (`writing-plans`) — it's effectively a sub-project.
- **MCP / satudata (§12.2)** — `MCPManager`, streamable-http, namespacing, failure isolation, large-payload→download_url convention. Own plan.
- **Deployment** — reverse proxy (same-origin), optional backend Dockerfile, prod secrets/env, Qdrant persistence.

---

## Frontend integration (after the backend endpoints exist — its own slice)

Tracked separately; do it once the backend is live so every call is verifiable:
real `api.ts` (fetch, swap mock) + `VITE_USE_MOCK` toggle · upload-stream types (`attachment_resolved`/`file_resolved`) · upload `File` bytes + send-sequencing (upload→chat) · auth refresh-on-load + `401→refresh→retry` + `credentials:"include"` · Vite `/api` proxy · KB poll-while-indexing.

---

## Self-review notes (spec coverage)

- §4 auth ✅ Stage 0; §5 sessions ✅ Stage 0; §2 envelope/timestamps → Stage 1.
- §6 ingress routing → Stage 6 (token routing) + §6.1 upload → Stage 6.
- §7 chat loop + retrieval tool → Stages 4–5; §8.1 ingestion → Stage 3; §8.3 KB → Stage 3; §8.4 session files/promote → Stage 6; §8.5 retrieval → Stage 4.
- §9.1 app DB ✅ Stage 0; §9.2 Qdrant → Stage 2.
- §10 security: scope filter → Stage 2/4; rate-limit → fold into Stage 5 route (add a limiter) or Phase 2.
- §11 storage → Stage 3 (`storage.py`). §12 stack → throughout. §12.2 MCP + §13 sandbox → Phase 2.
- §14 seed: extend the Stage 0 seed with KB files during Stage 3.
