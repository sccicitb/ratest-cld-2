# RAG Chat — Backend Specification

This document specifies the backend that replaces the mock layer in
[`src/lib/mock.ts`](../src/lib/mock.ts). It is written to be handed directly to
a coding agent (Codex, Claude Code, etc.) as an implementation brief.

**Status: LOCKED for v1 implementation.** Endpoint shapes, SSE event vocabularies,
data models, and the stack are final; subsequent work implements against this.

The frontend already exists and is fully functional against an in-memory mock.
**The contract below is exactly what the frontend expects.** Most of it is a
straight `api.ts` delegate swap; three integration seams the mock papered over
need real frontend work — enumerated in the §1 callout. Match the contract and
everything else in the UI is unchanged.

> **Architecture in one paragraph.** Retrieval from the knowledge base is an
> **agentic tool call** (`search_knowledge_base`) the model decides to make —
> not a hardcoded pipeline. Files reach the model two ways: small ones go
> **inline** (straight into context); large ones are **ingested** into a vector
> store and reached via the retrieval tool. There is one vector store with two
> faces — a **write side** (ingestion: chunk → embed → store) and a **read
> side** (the retrieval tool) — and a **scope** dimension separating the
> persistent Knowledge Base (`kb`) from per-conversation files (`session`).

> **Revision — self-hosted & model-agnostic (supersedes the first draft).**
> This revision targets a **self-hostable, vendor-neutral** stack. Concretely:
> - **Chat LLM is model-agnostic.** The tool loop is written against the
>   **OpenAI-compatible `/v1/chat/completions` shape** (works with vLLM, Ollama,
>   TGI, llama-server, or any hosted API), not a vendor SDK. Swapping models =
>   changing a base URL + model name.
> - **Embedding model is pinned, not swappable.** It is an independent component
>   from the chat LLM. Changing it invalidates every stored vector (full
>   re-index), so it is chosen once and frozen. Chosen model: **BGE-M3**
>   (embeddings) + **bge-reranker-v2-m3** (reranker), both run **in-process via
>   FlagEmbedding** — BAAI's official BGE-M3 library (torch; GPU) — which yields
>   BGE-M3's dense + sparse vectors in one pass (§8.1/§8.5). *(Earlier drafts said
>   "FastEmbed" — a library mix-up: Qdrant's FastEmbed is ONNX-only and does not
>   ship BGE-M3. FlagEmbedding is the model itself and is what the "dense+sparse
>   in one pass" design requires.)*
> - **Vector store is Qdrant** (was pgvector). **Relational/app data lives in a
>   separate app DB: SQLite for dev now, migratable to Postgres/MySQL later**
>   behind an ORM + migrations + repository layer (§9.1); Postgres is no longer
>   assumed.
> - Everything above changes only the **implementation**. The §1–§5 frontend
>   contract, SSE event shapes, and data models are **unchanged**.

---

## 1. Frontend integration contract

The frontend talks to the backend through a single file:
[`src/lib/api.ts`](../src/lib/api.ts). Every function there currently delegates
to `mock.*`. To go live, replace each delegate with a `fetch` to the endpoints
below. Specifics:

1. **Base URL** — `import.meta.env.VITE_API_BASE_URL` (e.g.
   `http://localhost:8000/api`).
2. **Auth token** — held in memory by the auth store
   ([`src/lib/auth.ts`](../src/lib/auth.ts) → `getToken()`). Send it as
   `Authorization: Bearer <accessToken>` on every authenticated request.
3. **Streaming** — `streamChat()` returns an `AsyncGenerator<StreamEvent>`. It
   fetches the SSE endpoint (§7) and yields parsed `StreamEvent` objects whose
   shapes match [`src/types/chat.ts`](../src/types/chat.ts) exactly.
4. **New endpoints the integration layer must add** beyond the existing mock
   delegates:
   - `POST /sessions/:id/attachments` — multipart → SSE attachment upload (§6.1).
   - `GET /sessions/:id/files` / `POST /sessions/:id/files/:fileId/promote` —
     session-scoped files (`getSessionFiles` / `promoteSessionFile`, already in
     `api.ts`).
   - `POST /auth/refresh` — silent session restore (§4).
   The KB-upload and chat endpoints also become SSE (were plain in the mock).

On app load (including a hard refresh) the frontend calls `POST /auth/refresh`
(§4) using the httpOnly refresh cookie to silently restore the session; only if
that fails is the user routed to `/login`. The access token itself is held in
memory; the refresh token lives in the cookie, not in JS-readable storage.

> **Frontend work beyond `api.ts` (the "UI unchanged" caveat is not absolute).**
> The mock papered over three integration seams that need real frontend changes:
> 1. **File bytes.** The composer currently **discards the `File` blobs** —
>    `InputBar` sends only `Attachment` metadata (`url: "#"`) + `IngestFile
>    {name,size}`. The real app must upload the bytes at send (§6.1).
> 2. **Send sequencing.** Today `handleSend` fires ingestion and the chat turn
>    concurrently. The real flow is **upload → then chat** (§6.1).
> 3. **Auth.** A refresh-on-load call + a `401 → refresh → retry` interceptor,
>    all requests using `credentials: "include"` (§4).
>
> Everything else is a straight `api.ts` delegate swap.

---

## 2. Conventions

| Aspect | Rule |
|---|---|
| Base path | All routes under `/api`. |
| Auth | `Authorization: Bearer <jwt>` on every route except `POST /auth/login` and `POST /auth/refresh` (which authenticate via the httpOnly refresh cookie, not a bearer). |
| Content type | JSON for requests/responses; SSE endpoints return `text/event-stream`; uploads use `multipart/form-data` **and respond with `text/event-stream`** (§6.1, §8.3). |
| Timestamps | ISO-8601 UTC strings (`createdAt`, `updatedAt`, `uploadDate`). |
| IDs | Opaque strings (UUID v4 recommended). |
| Casing | JSON keys are **camelCase** (frontend types are camelCase). |
| CORS / origin | **Same-origin in deployment** — SPA + API behind one reverse proxy, so CORS is effectively moot in prod. In dev, the **Vite dev server proxies `/api` → the backend**, so the browser still sees a single origin. The refresh cookie is therefore `SameSite=Lax` (§4). *Only* if you later split origins do you need `Access-Control-Allow-Credentials: true` + exact-origin reflection (never `*`) and `SameSite=None; Secure`. |

### Error format

Non-2xx responses return JSON; the frontend's `ApiError` carries `status`,
`message`, `code`.

```json
{ "message": "Session not found", "code": "not_found" }
```

| Status | When |
|---|---|
| 400 / 422 | Validation error. |
| 401 | Missing/invalid/expired token. |
| 403 | Authenticated but not allowed. |
| 404 | Resource does not exist (or not owned by the caller). |
| 409 | Conflict. |
| 413 | Upload exceeds max size. |
| 415 | Unsupported file type. |
| 500 | Unexpected. |

---

## 3. Data models

Mirror [`src/types/api.ts`](../src/types/api.ts),
[`src/types/chat.ts`](../src/types/chat.ts), and
[`src/types/kb.ts`](../src/types/kb.ts). **Do not rename or drop fields.**

### User
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `email` | string | unique |
| `displayName` | string | |
| `avatarUrl` | string? | optional |

### AuthResponse
`{ accessToken: string (JWT), user: User }`

### Session
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `title` | string | defaults to `"New Chat"` |
| `createdAt` / `updatedAt` | string (ISO) | `updatedAt` bumped on new message / rename |

### Attachment
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `fileName` | string | |
| `fileType` | string | MIME |
| `fileSize` | number | bytes |
| `url` | string | served/downloadable URL |
| `thumbnailUrl` | string? | optional |
| `ingested` | boolean? | **true** = file was too big to inline and was ingested session-scoped (reached via the search tool); **false/absent** = inlined into context. Drives the "Indexed" badge in the message bubble. |

### Message
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `sessionId` | string | |
| `role` | `"user" \| "assistant" \| "system"` | |
| `content` | string | Markdown for assistant messages |
| `attachments` | Attachment[]? | on user messages with files (both inline and ingested) |
| `createdAt` | string (ISO) | |

### KnowledgeBaseFile
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `name` | string | |
| `size` | number | bytes |
| `uploadDate` | string (ISO) | |
| `chunkCount` | number | `0` while indexing/error |
| `status` | `"indexing" \| "ready" \| "error"` | |
| `tags` | string[] | lowercase, de-duplicated |
| `scope` | `"kb" \| "session"`? | **`kb`** (default) = persistent, shown on KB page, retrievable everywhere. **`session`** = ingested from a chat attachment; retrievable in that conversation only, never on the KB page. |

### StreamEvent (SSE discriminated union)
```ts
{ type: "step", step: PipelineStep, status: "active" | "complete",
  id?: string, toolName?: string, toolArgs?: Record<string, unknown> }
{ type: "token", content: string }
{ type: "chunk_progress", fileName: string, progress: number,
  chunkCount: number, total: number }
{ type: "done", messageId: string }
{ type: "error", message: string }

type PipelineStep =
  | "thinking" | "retrieving_context" | "calling_tool" | "generating_response";
```

`StepEvent.id` keys steps that occur **more than once per turn** (e.g. several
`calling_tool` invocations). Reuse the same `id` for a step's matching
`active`/`complete` pair. Single-occurrence steps (`thinking`,
`generating_response`) may omit `id` — the UI keys them by `step`.

> Note: the chat stream **does not** emit `chunk_progress` — ingestion progress
> comes from the **upload stream** below (matching the mock, where
> `mockIngestChatFile` is a separate generator from `mockStreamChat`).
> `chunk_progress` stays in the union as the shared shape.

### Upload-stream events (separate endpoints, separate vocabulary)

The attachment-upload (§6.1) and KB-upload (§8.3) endpoints are **also** SSE, but
they are different endpoints with different consumers and use their own small
event set — they do **not** reuse the chat `StreamEvent` union:

```ts
{ type: "chunk_progress", fileName: string, progress: number,
  chunkCount: number, total: number }          // per ingesting file (shared shape)
{ type: "attachment_resolved", attachment: Attachment }       // chat upload (§6.1) — authoritative `ingested`
{ type: "file_resolved", file: KnowledgeBaseFile }            // KB upload (§8.3) — the ready/error record
{ type: "done" }
{ type: "error", message: string }
```

---

## 4. Authentication endpoints

**Locked: access + refresh JWT with rotation.** This **intentionally deviates**
from the original memory-only mock (§1) to fix the hard-refresh logout.
- **Access token** — short-lived JWT (~15 min), `sub = user.id`, held **in
  memory** by the frontend, sent as `Authorization: Bearer`.
- **Refresh token** — long-lived (~7–30 days), opaque, set as an **httpOnly,
  Secure, `SameSite=Lax`** cookie scoped to `Path=/api/auth` (JS can't read it).
  `Lax` is sufficient because the SPA and API share an origin (reverse proxy in
  prod; Vite `/api` proxy in dev — §2). Store a **hash** server-side in
  `refresh_tokens` (§9.1) so it can be revoked. *(Split-origin deployments only:
  switch to `SameSite=None; Secure` + CORS credentials.)* The frontend sends all
  auth + authed requests with `credentials: "include"`.

### `POST /api/auth/login`
Mock: `mockLogin`. Verify email + password hash; return the **access token** in
`AuthResponse`, and **set the refresh cookie**.
- **200:** `AuthResponse` · **401:** invalid credentials.

### `POST /api/auth/refresh`
Reads the refresh cookie (no body). Validate it against the stored hash; if valid,
**rotate** — issue a new access token *and* a new refresh token, invalidate the
old refresh token (reuse of an old one ⇒ treat as compromise, revoke the chain).
The frontend calls this (a) automatically on a **401** then retries the original
request, and (b) **on app load** to restore the session silently.
- **200:** **`AuthResponse`** (`{ accessToken, user }`) + new refresh cookie ·
  **401:** missing/invalid/revoked.

  Returns the full `AuthResponse` (not just the token) so app-load restore is a
  single round-trip — no follow-up `GET /me` needed.

### `GET /api/auth/me`
Mock: `mockGetMe`. Validate the bearer access token.
- **200:** `User` · **401:** missing/invalid/expired.

### `POST /api/auth/logout`
Reads the refresh cookie (no bearer required), revokes the stored refresh token,
and clears the cookie. Idempotent — a missing/already-revoked token still returns
200.
- **200:** empty.

---

## 5. Sessions & messages endpoints

All require auth and are **scoped to the authenticated user** (return 404 for
other users' sessions — never leak existence).

| Method · Path | Mock | Behavior |
|---|---|---|
| `GET /api/sessions` | `mockGetSessions` | `Session[]`, sorted by `updatedAt` **desc** |
| `GET /api/sessions/:id` | `mockGetSession` | `Session` · 404 |
| `POST /api/sessions` | `mockCreateSession` | new empty session titled `"New Chat"` |
| `PATCH /api/sessions/:id` | `mockRenameSession` | body `{ title }`; bumps `updatedAt` |
| `DELETE /api/sessions/:id` | `mockDeleteSession` | cascade-deletes messages **and session-scoped files/chunks** |
| `GET /api/sessions/:id/messages` | `mockGetMessages` | `Message[]`, chronological |

---

## 6. Content ingress — how files reach the model

This is the decision that shapes the whole system. There are **two paths** for
getting content in front of the model, chosen by **token cost**, not file type
or megabytes:

| Path | For | Mechanism |
|---|---|---|
| **Inline** | small files | parse → put the whole thing in the prompt (text, or image/PDF as a native vision/document block) |
| **Retrieval** | large files | chunk → embed → store → model pulls relevant pieces via the `search_knowledge_base` tool |

### The routing rule is token-based, not byte-based

**Bytes ≠ tokens.** An image-heavy 30 MB PDF can be only a few thousand tokens
(thin text layer) — or hundreds of thousands (if every page is rendered for
vision). So the authoritative inline-vs-ingest decision is made **server-side,
after extraction, on the resulting token count**:

```
extract/parse the file  →  count tokens  →
    tokens ≤ INLINE_TOKEN_BUDGET   → inline
    tokens >  INLINE_TOKEN_BUDGET   → ingest (session-scoped, §8)
```

`INLINE_TOKEN_BUDGET` is configurable; **default 6000 tokens** (room for a few
pages of text inline without crowding the context window — tune per deployed
model). The frontend cannot count tokens, so its byte thresholds
([`utils.ts`](../src/lib/utils.ts): `routeChatAttachment`) are only a **crude
proxy + sanity ceiling** — never the source of truth. The backend re-decides.

### Two entry points (don't conflate them)

Files arrive by **two distinct paths**, and they route differently:
- **Chat composer** (§7/§8.4) — has the inline-vs-ingest fork above; ingested
  files are `scope: session`.
- **KB page** (§8.3) — **always ingested**, `scope: kb`, persistent. **No inline
  option** (KB docs are only ever retrieved as text chunks).

### Extraction routing — parser vs OCR vs vision (locked)

What turns a file into model-usable content depends on the file **and** the
entry path. The trigger for OCR is **"scanned / no usable text layer,"** not size:

| File | Chat — small (inline) | Chat — large (ingest) | KB page (always ingest) |
|---|---|---|---|
| Text-layer PDF / text files | **PyMuPDF** → inline text | PyMuPDF → chunk → embed | PyMuPDF → chunk → embed |
| Scanned / image-only PDF | **Surya OCR** → inline text | Surya OCR → chunk → embed | Surya OCR → chunk → embed |
| Standalone image (`.png/.jpg`) | **Qwen vision** (reads pixels live) | rare | **N/A — not a KB type** |

Rules that fall out of this:
- **Detect, don't guess:** parse with PyMuPDF first, measure text density; route to
  **Surya** only when the text layer is thin/absent. A big *text* PDF still uses
  PyMuPDF (faster, more accurate than OCR).
- **Scanned → always Surya** (even when small): page-images cost ~1.5k–4.8k tokens
  *each*, so OCR-to-text beats inlining a multi-page scan as vision.
- **Vision is chat-only.** A standalone image has no inline escape hatch on the KB
  page, so **images are not a KB upload type** (see §8.3). They remain valid chat
  attachments, read live by Qwen.
- **Surya licensing caveat:** code is Apache-2.0, but model **weights are AI Pubs
  Open Rail-M** — free under $5M funding/revenue, paid license above. If this goes
  commercial past that bar, fall back to Tesseract or Qwen-vision-as-OCR.

Surya and Qwen-vision both require their runtime to be present: Surya runs
in-process in the ingestion worker (auto-downloads weights); Qwen vision requires
the llama-server endpoint to have the multimodal projector (`mmproj`) loaded.

### Three outcomes the composer produces (frontend → backend)

| Outcome | Frontend signal | Backend action |
|---|---|---|
| **inline** | attachment in the message `attachments[]`, `ingested` absent/false | parse → include in the model's context for that turn |
| **ingest** | attachment in `attachments[]` with `ingested: true`, **and** the file is uploaded for session-scoped ingestion | run the ingestion pipeline scoped to the session (§8) |
| **reject** | not sent | n/a (frontend blocks unsupported types / past the hard ceiling) |

> The model never "ingests." Ingestion is event-driven (an upload), not a tool
> the model calls. Only **retrieval** is a tool.

> The frontend's `routeChatAttachment` (byte-based) is only a **preview**. The
> **backend re-decides** inline vs ingest by token count during upload (§6.1)
> and returns the authoritative `ingested` flag. `reject` stays purely
> frontend-side (blocked before any upload).

### 6.1 Attachment upload protocol (multipart → SSE, at send)

Bytes are uploaded **when the message is sent**, not when the file is attached.
When the message has **no attachments, Step 1 is skipped** — go straight to chat.
Otherwise the frontend performs two **sequential** steps:

**Step 1 — upload.** `POST /api/sessions/:id/attachments`, `multipart/form-data`
with one or more `files`. **Response is `text/event-stream`** (upload-stream
events, §3). The backend is the **routing authority** — per file it stores the
blob (→ `url`/`storage_key`), parses, counts tokens (§6), then:

| Decision | Backend action | Events emitted (for that file) |
|---|---|---|
| **inline** (≤ `INLINE_TOKEN_BUDGET`) | persist an `attachment` record | `attachment_resolved` with `ingested: false` |
| **ingest** (> budget) | create a `kb_files` row (`scope: session`, `status: indexing`), run the ingestion pipeline (§8.1) | `chunk_progress` (repeated) → `attachment_resolved` with `ingested: true` |

The stream ends with `done` once every file is resolved. This is where the chat
feed's inline `ChunkingProgress` rows come from — the **upload** stream, not the
chat stream.

**Step 2 — chat.** After the upload stream **closes**, `POST
/api/sessions/:id/chat` with the message and the **resolved** attachments (§7).

**Ordering is deliberate (upload fully completes before chat).** A freshly
uploaded session file must be retrievable in the *first* turn ("summarize the
file I just sent"), so the chat turn starts only after ingestion finishes.
Trade-off: a large PDF delays the first answer by its indexing time — acceptable,
and the progress UI covers the wait. *(Async alternative — start chat
immediately and let the file flip `ready` mid-turn — rejected for v1: the first
retrieval could miss the file.)*

**Orphans.** Files uploaded but never sent (send cancelled) leave unbound
`attachment` rows / `indexing` session files — sweep them on a TTL. Bound session
files are cleaned with their session (§8.2).

---

## 7. Chat streaming (SSE) — agentic tool-calling RAG

### `POST /api/sessions/:id/chat`
Mock: `mockStreamChat`. A **Server-Sent Events** stream that runs the model's
**tool-use loop** and emits `StreamEvent`s.

- **Request headers:** `Authorization`, `Accept: text/event-stream`.
- **Body** (`application/json`):
  ```json
  {
    "message": "How does our pricing compare to last quarter?",
    "attachments": [
      { "id": "att-1", "fileName": "q2.pdf", "fileType": "application/pdf",
        "fileSize": 240000, "url": "/api/files/att-1", "ingested": false }
    ]
  }
  ```
  These are the **resolved** attachment records returned by the preceding upload
  step (§6.1) — the bytes are already stored/indexed. Inline attachments
  (`ingested: false`) are loaded into the model's context for this turn; ingested
  ones (`ingested: true`) are *not* inlined — they're reached via the retrieval
  tool. The backend looks up each attachment's content by `id` (never re-uploaded
  here).
- **Response:** `text/event-stream`, one JSON `StreamEvent` per `data:` line,
  blank line between events, stream closed after `done`:
  ```
  data: {"type":"step","step":"thinking","status":"active"}

  data: {"type":"token","content":"Based "}

  data: {"type":"done","messageId":"msg_abc"}
  ```

### The retrieval tool

Define one tool and let the model decide when to call it. Use the
**OpenAI-compatible `tools` schema** (a `function` object), so the same
definition works against any backend that speaks `/v1/chat/completions`:

```json
{
  "type": "function",
  "function": {
    "name": "search_knowledge_base",
    "description": "Search the user's indexed documents. Call this whenever the answer may depend on the user's files, recent data, or anything not already in the conversation. May be called multiple times with refined queries.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string", "description": "Semantic search query" },
        "tags":  { "type": "array", "items": { "type": "string" } }
      },
      "required": ["query"]
    }
  }
}
```

`search_knowledge_base` is a **client-side tool**: your backend executes it (so
it can inject scope — see below) and returns the result to the model as a
`role: "tool"` message carrying the matching `tool_call_id`.

**Scope is injected server-side, not by the model.** When the model calls the
tool, the backend embeds the query with BGE-M3 (dense + sparse) and runs a
**hybrid search in Qdrant**, filtered to this user's `kb` files **plus** this
conversation's `session` files. The filter is applied on the chunk **payload**
(scope fields are denormalized onto every point — see §8.1/§9.2), since Qdrant
has no joins:

```
query_outputs = bge_m3.encode(query)          # → {dense, sparse}

qdrant.query_points(
  collection = "kb_chunks",
  prefetch = [
    Prefetch(query=dense_vec,  using="dense",  limit=50),
    Prefetch(query=sparse_vec, using="sparse", limit=50),
  ],
  query  = FusionQuery(fusion=RRF),           # merge dense + sparse
  filter = Filter(must=[
    Match("user_id", :user),
    Match("status",  "ready"),
    Should([                                  # kb OR (session AND this session)
      Match("scope", "kb"),
      Must([ Match("scope","session"), Match("session_id", :session) ]),
    ]),
  ]),
  limit = :k,                                 # final top-k (e.g. 5)
  with_payload = True,
)
```

The model never sees a session id. See §8.5 for the full two-stage read side
(recall → fuse → optional rerank). (Optional future nicety: expose a
`scope: "all" | "kb" | "session"` hint on the tool so the model can deliberately
narrow to "the file I just uploaded"; default `"all"`. Not required for parity.)

### Pipeline → event mapping

Run a **manual tool-use loop** against your OpenAI-compatible chat endpoint (no
vendor SDK). Each pass: call the model with `messages` + the `tools` array; if
the response has `tool_calls`, execute each, append a `role: "tool"` message per
`tool_call_id`, and loop; otherwise stream the final text and stop. A manual
loop (rather than a framework's auto-runner) is deliberate here — you need to
emit your own SSE step events at each seam. The number of `calling_tool` events
is **dynamic** (0..n):

| Loop event | SSE event(s) |
|---|---|
| Planning / query understanding begins → ends | `step thinking active` → `complete` |
| Model returns a `search_knowledge_base` tool call | `step calling_tool active` with a **unique `id`**, `toolName: "search_knowledge_base"`, `toolArgs: { query, scope }` — `query` is the model's argument; **`scope` is injected by the backend for display only** (it is not a tool `parameter`, §7 tool def), reflecting the scope filter actually applied (`"kb"` vs `"this chat + KB"`) |
| You run the vector search for that call | (optionally) `step retrieving_context active` → `complete` |
| You return the tool result; that call resolves | `step calling_tool complete` (same `id`) |
| Model generates the answer | `step generating_response active`, then `token` per delta, then `complete` |
| Loop ends (model returns a normal message with no `tool_calls`) | `done` with the persisted assistant `messageId` |

A trivial message (e.g. "thanks") may produce **zero** tool calls — emit only
`thinking` → `generating_response` → `done`. A compound question may produce
**several** `calling_tool` pairs, each with its own `id`.

### Loop implementation (locked)

**Ground-up, not a framework.** The loop is hand-written (~60 lines) so it owns
the SSE seams and scope injection. Lean on libraries for the components — OpenAI
SDK (pointed at `MODEL_BASE_URL`) for the model call, official `mcp` SDK for MCP,
FlagEmbedding / qdrant-client / SQLAlchemy for the rest — but **buy the
components, build the orchestration**. No LangChain/LangGraph (single-agent; revisit only if
this ever goes multi-agent).

**Turn-by-turn** (an async generator yielding `StreamEvent`s):
1. Persist user message; auto-title if `"New Chat"`; build `messages` (history +
   this turn); ask the registry for enabled tool schemas. Emit `thinking active`.
2. **Call the model** (streaming) with `messages` + `tools`.
3. Branch:
   - **No `tool_calls`** → final answer. Emit `thinking complete` →
     `generating_response active`, stream each delta as `token`, then
     `generating_response complete`. Break.
   - **`tool_calls` present** → for each, emit `calling_tool active` (unique
     `id`, `toolName`, `toolArgs`); run `registry.execute(name, args, context)`
     — **`context` injects `user_id`/`session_id` here, never from the model** —
     emit `calling_tool complete` (same `id`). Append the assistant tool-call
     message + each `role:"tool"` result to `messages`. Loop to step 2.
4. Persist assistant message; emit `done` with its `messageId`; close.

**Interleaving rule (locked).** A model turn may contain text *and* tool calls.
**Only stream text deltas as `token` events on a turn with no tool calls**; on
tool-calling turns, suppress/buffer interim text. (Final-answer-only streaming —
matches the existing contract.)

**Parallel tool calls.** The model may return several `tool_calls` in one turn;
execute them concurrently, each with its own event `id` (that's what
`StepEvent.id` is for).

**Safety rails (configurable):**
- `MAX_TOOL_ITERATIONS` (default **6**) — max sequential model→tool→model rounds
  per turn; on hitting it, force a final answer (no more tools offered). Set
  generous on purpose: a legitimate chain (KB search → satudata info → data →
  `execute_code` → answer) can be ~5 rounds, so this is a runaway ceiling, not a
  budget for normal use.
- `MAX_PARALLEL_TOOLS` (default **2**) — max tool calls executed concurrently in
  one turn; deliberately small to protect the server (sandbox / MCP / scraper are
  the expensive tools). Excess calls queue and run in batches.

### Server-side side effects (must match the mock)
1. Persist the **user message** immediately (with its attachments, inline +
   ingested).
2. If the session title is still `"New Chat"`, auto-title from the first user
   message (truncate ~40 chars). Bump `updatedAt`.
3. Run the tool loop, streaming events.
4. Persist the **assistant message** with the full accumulated `content`; bump
   `updatedAt`.
5. Emit `done` with the assistant `messageId`.

On failure mid-stream emit `{ "type": "error", "message": "<safe>" }` and close
(don't throw an HTTP error after streaming has started). Disable proxy buffering
so events flush immediately; send SSE keep-alive comments on long gaps. On client
disconnect, cancel the model call.

---

## 8. Knowledge base, ingestion & retrieval

### 8.0 One store, two faces — and not (necessarily) a separate service

There is **one vector store** (Qdrant). It has a **write side** (ingestion) and a
**read side** (the retrieval tool); they meet at the store. File **metadata**
lives in the app DB (§9.1); chunk **vectors** live in Qdrant (§9.2).

```
WRITE (ingestion, event-driven)        READ (model-driven)
upload → parse → chunk → embed         search_knowledge_base tool
        ↓                                       ↑
        [ Qdrant chunks + app-DB kb_files ] ────┘
```

Ingestion does **not** need to be its own service. Start with a **background
worker** (queue + worker in the same app): the upload endpoint returns
immediately with `status: "indexing"`, the worker does the heavy parsing/
embedding, then flips the file to `ready`. (The UI already expects this — the
`indexing → ready` badge *is* this contract.) Split ingestion into a dedicated
service only when the work demands it (heavy OCR/embedding, GPU, bursty load,
independent scaling). A capable GPU is an argument to route **more** to
ingestion, not to inline bigger files — it speeds ingestion; it does nothing for
the model's context window.

### 8.1 Ingestion pipeline (write side)

Triggered by KB upload, KB reindex, and session-scoped chat ingestion. Steps:

1. **Extract** — per type: **text-layer PDF → PyMuPDF**; `.docx` (`python-docx`);
   TXT/MD/CSV/JSON directly. **`.doc` (legacy Word)** is not readable by
   python-docx — convert it first via **LibreOffice headless**
   (`soffice --headless --convert-to docx`) in the worker, then parse the result.
   (If the LibreOffice dependency is unwanted, drop `.doc` from the supported set
   in §8.3 and `SUPPORTED_FILE_TYPES`.)
2. **Text-layer detection → parser vs OCR** — parse the PDF and measure text
   density. If it has a real text layer, use the extracted text. If it's
   **scanned / image-only** (thin or no text), route to **Surya OCR → text**
   (in-process in the worker; weights auto-download). Either way the output is
   **text**, which flows into the same chunk→embed path — BGE-M3 is text-only, so
   there is no separate image-embedding branch. (Standalone images are chat-only
   via Qwen vision, never ingested — §6/§8.3.)
3. **Chunk** — ~500–1000 tokens, ~10–15% overlap, split on structure.
4. **Embed** — **BGE-M3** via **FlagEmbedding** (`BGEM3FlagModel`), running
   **in-process** with torch — no embedding server to host. **Device is
   auto-detected** (CUDA → MPS → CPU) with a config override (`EMBED_DEVICE`), so
   the same code runs on Windows+CUDA (dev & prod), Mac M1 (MPS), and CPU. **fp16
   only on CUDA** (`use_fp16` derived from the resolved device — never hardcode
   `True`; it's unsupported/slow on MPS/CPU). One `model.encode(...,
   return_dense=True, return_sparse=True)` call emits **dense (1024-dim) +
   sparse** vectors in one pass — store both (§9.2).
   *(Install note: `pip/uv` gives Mac the MPS/CPU torch wheel automatically; on
   Windows/Linux+CUDA install the CUDA torch wheel from PyTorch's index to use
   the GPU.)* (BGE-M3 can also emit ColBERT/multi-vectors via
   `return_colbert_vecs`, but v1's reranker is a cross-encoder, so they are
   **not** generated or stored — §8.5.) The **same pinned model** is used for
   indexing and querying — a mismatch silently breaks retrieval. BGE-M3 needs
   **no** query/passage prefixes. (Scanned pages are already text by this point,
   via Surya in step 2 — BGE-M3 embeds that text like any other.)
5. **Store** — upsert each chunk as a **Qdrant point**: its dense + sparse
   vectors, plus a **payload** carrying `content`, `file_id`, `chunk_idx`,
   `tags`, and the **denormalized scope fields** (`user_id`, `scope`,
   `session_id`, `status`). These are copied onto **every** point because Qdrant
   has no joins and retrieval filters on them directly (§7). **Consequence:**
   any change to a file's scope/status (reindex, or **promote session → kb**,
   §8.4) must update the payload on **all** of that file's points, not one row.
6. **Finalize** — `status: "ready"`, `chunkCount = <#chunks>`; on failure
   `status: "error"`, `chunkCount: 0`.

Optionally stream `chunk_progress` events during ingestion (the chat UI renders
them inline; the type already exists). Reindex re-runs this pipeline.

### 8.2 Scopes

- **`kb`** — uploaded via the Knowledge Base page; persistent; on the KB page;
  retrievable from every chat.
- **`session`** — ingested from a chat attachment too big to inline; retrievable
  **only** in that conversation; **not** on the KB page. **Retention: no TTL** —
  session files live exactly as long as their session (history is preserved).
  They are removed **only** when the session is deleted: the delete handler must
  remove that session's **Qdrant points first** (filter by the `session_id`
  payload), *then* cascade the app-DB rows — no FK spans the two stores, so the
  app enforces this ordering (Qdrant-first leaves at worst a harmless orphan DB
  row on mid-failure, never searchable-but-unreachable chunks). A chat
  attachment's *origin* sets this scope — it inherits `session` by default; the
  user may **promote** it to `kb`.

### 8.3 Knowledge Base endpoints (scope `kb`)

| Method · Path | Mock | Behavior |
|---|---|---|
| `GET /api/knowledge-base` | `mockGetKBFiles` | `KnowledgeBaseFile[]` (scope `kb` only), sorted by `uploadDate` **desc**; filters `search` / `status` / `tag` (AND) |
| `POST /api/knowledge-base/upload` | `mockUploadKBFile` | `multipart/form-data` (`file`); validate type (415) + size (413). **Response is `text/event-stream`**: create the `indexing` record, run §8.1 streaming `chunk_progress`, then emit `file_resolved` with the `ready` record (or `error`), then `done`. The KB page's upload cards consume this — **no separate polling needed**. (Network-upload % is a client concern via XHR upload events; the SSE covers the indexing phase.) |
| `POST /api/knowledge-base/:id/reindex` | `mockReindexKBFile` | set `indexing`, re-run §8.1, back to `ready` with new `chunkCount` |
| `PATCH /api/knowledge-base/:id/tags` | `mockUpdateFileTags` | body `{ tags }` (lowercase + de-dupe) |
| `DELETE /api/knowledge-base/:id` | `mockDeleteKBFile` | delete file, blob, and chunks |

**KB-page supported types are documents only:** `.pdf .md .txt .docx .doc .csv
.json`. **Images (`.png .jpg .jpeg`) are deliberately excluded from KB uploads** —
a standalone image has no inline/vision path on the KB page and BGE-M3 can't embed
pixels, so it would be unsearchable. Images remain valid **chat** attachments
(read live by Qwen vision; §6). `SUPPORTED_FILE_TYPES` in
[`utils.ts`](../src/lib/utils.ts) still lists images for the composer; the KB
upload endpoint must validate against the **document-only** subset (415 otherwise).

**Observing `indexing → ready` (not just on upload).** The upload SSE delivers the
just-uploaded file's `ready` state, but **reindex** and any file *already* sitting
in `indexing` (e.g. a seed file, or a reindex triggered elsewhere) have no stream
attached. So the rule is: **the KB list query refetches on an interval (~2–3 s)
while any file is `indexing`, and stops when none are.** This is the general
observation path (covers upload, reindex, and pre-existing); the upload SSE is the
bonus that adds *granular* progress for the active upload. `POST /reindex`
therefore just returns the updated `indexing` record (JSON, not SSE) — the poll
picks up the flip. Frontend: `useKnowledgeBaseFiles` gets a conditional
`refetchInterval`; `useReindexKBFile` already invalidates the list.

### 8.4 Session-scoped file endpoints (scope `session`)

| Method · Path | Mock | Behavior |
|---|---|---|
| ingestion | `mockIngestChatFile` | **No separate endpoint** — session ingestion happens inside the attachment-upload stream (`POST /api/sessions/:id/attachments`, §6.1): files routed to `ingest` run §8.1 with `scope = session`, `session_id = :id`, streaming `chunk_progress` then `attachment_resolved`. |
| `GET /api/sessions/:id/files` | `mockGetSessionFiles` | `KnowledgeBaseFile[]` for this session (scope `session`); powers the **"This chat's files"** section of the Sources drawer |
| `POST /api/sessions/:id/files/:fileId/promote` | `mockPromoteSessionFile` | flip a session file to `scope = kb` (the "Save to Knowledge Base" action); returns the promoted `KnowledgeBaseFile` |

> The frontend currently has the promote endpoint wired in the data layer but no
> UI button yet — implement the endpoint regardless.

### 8.5 Retrieval (read side) — two-stage hybrid

The `search_knowledge_base` tool body. The principle: **cast a wide cheap net,
then optionally re-score precisely.**

1. **Recall (always).** Embed the query with BGE-M3 → dense + sparse. Run both
   as Qdrant `prefetch` legs (top ~50 each) under the **scope filter** (§7), and
   **fuse** with **RRF** into a single ranked list. Dense gives semantic
   matches; sparse catches exact terms (codes, filenames, names). This stage
   alone is a strong default — ship it first.
2. **Rerank.** The rerank stage may be enabled later (recall alone ships first),
   but the model is **locked: `BAAI/bge-reranker-v2-m3`** — Apache-2.0
   (commercial-safe), built on bge-m3 (same family as the embedder), explicitly
   supports Indonesian, 512-token input, ~2.27 GB. Run it **in-process via
   FlagEmbedding's `FlagReranker`** (`FlagReranker("BAAI/bge-reranker-v2-m3",
   use_fp16=True)` → `.compute_score([[query, passage], ...])`). It re-scores the
   fused top ~50 down to the final top-k. (Rejected alternative:
   `jina-reranker-v2-base-multilingual` — CC-BY-NC 4.0, non-commercial, so
   unusable here.)

Return the final top-k chunk `content` (with `file_id`/`chunk_idx` for citation)
as the tool result. **Start with no reranker**; add one later behind a flag.

---

## 9. Persistence — two stores

Persistence now splits across **two** stores (pgvector held both jobs before; it
held neither now). The vector store is decided (**Qdrant**); the **app data
store is SQLite for now, migratable to Postgres/MySQL later** — see §9.1.

### 9.1 App data store (SQLite now → Postgres/MySQL later)

Holds everything **except** chunk vectors: `users`, `sessions`, `messages`,
`attachments`, and `kb_files` **metadata**. **Locked decision: start on SQLite**
(zero-ops, fast dev), engineered so a later switch to Postgres/MySQL is a
config change, not a rewrite. Three layers make that cheap:

1. **ORM: SQLAlchemy** (or **SQLModel** with FastAPI). Define tables once; switch
   engine via the connection string.
2. **Migrations: Alembic from day one**, even on SQLite — versioned, portable
   schema; standing up the target DB later is "run the migrations."
3. **Repository pattern** — all data access behind `SessionRepo`, `MessageRepo`,
   `KBFileRepo`, etc. The rest of the app never touches SQL, so an engine swap
   touches only the repo layer.

**Portability rules (so the migration stays minimal):**
- **`tags` must not be a native array** — Postgres has arrays, SQLite/MySQL do
  not. Store as **JSON** (SQLAlchemy `JSON`) or a child `kb_file_tags` table.
- **UUIDs as text**, not a Postgres-only `uuid` type.
- **Booleans/timestamps via SQLAlchemy types**, not raw SQLite affinities.
- **Don't rely on SQLite single-writer concurrency** — the ingestion worker and
  API both write; enable **WAL** mode and design as if a client/server DB is
  underneath.

Logical schema (engine-neutral; `(pk)`/`(fk)` and types are illustrative):

```
users:       id (pk), email (unique), display_name, avatar_url?, password_hash, created_at
refresh_tokens: id (pk), user_id (fk→users, cascade), token_hash, expires_at,
             revoked=false, created_at   [idx: (user_id), (token_hash)]
sessions:    id (pk), user_id (fk→users, cascade), title="New Chat", created_at, updated_at
             [idx: (user_id, updated_at desc)]
messages:    id (pk), session_id (fk→sessions, cascade), role(user|assistant|system), content, created_at
             [idx: (session_id, created_at)]
attachments: id (pk), message_id (fk→messages, cascade), file_name, file_type, file_size,
             url, thumbnail_url?, ingested=false
kb_files:    id (pk), user_id (fk→users, cascade), scope(kb|session)="kb",
             session_id? (fk→sessions, cascade; set when scope=session),
             name, size, upload_date, chunk_count=0, status(indexing|ready|error),
             tags (JSON or child table — NOT a native array), storage_key
             [idx: (user_id, scope, upload_date desc), (session_id)]
             # NOTE: no `modality` column in v1 — every ingested file becomes
             # text (PyMuPDF or Surya OCR), so there is no text-vs-multimodal
             # distinction to store. Add one only if a visual-embedding path
             # (e.g. ColBERT page-images) is introduced later.
```

The previous `kb_chunks` table is **gone** — chunks now live in Qdrant (§9.2).

### 9.2 Vector store (Qdrant)

One collection, **named vectors**, with the scope fields denormalized onto each
point's payload (§8.1):

```
collection "kb_chunks":
  vectors:
    dense   → size 1024, distance Cosine      # BGE-M3 dense
  sparse_vectors:
    sparse                                     # BGE-M3 sparse (BM25-style)
  # NOT in v1 — the chosen reranker is a cross-encoder (bge-reranker-v2-m3, §8.5),
  # which re-scores fetched chunk TEXT and needs no stored vectors. A `colbert`
  # multivector (comparator MaxSim) would only be added if you later switch to
  # late-interaction reranking. Do not create it now.

  payload (per point):
    content    : string
    file_id    : string          # references kb_files.id in the app DB
    chunk_idx  : int
    tags       : string[]
    user_id    : string          # denormalized — retrieval filters on these
    scope      : "kb" | "session"
    session_id : string | null
    status     : "indexing" | "ready" | "error"
  payload indexes: user_id, scope, session_id, status
```

**Cross-store consistency (app-enforced — no FKs span the two stores):**
deleting a `kb_files` record or cascading a session delete must also delete that
file's Qdrant points; promote (`session → kb`) and reindex must update the
payloads on all of that file's points (§8.1).

Serialize app-DB records to the camelCase models in §3 at the API boundary (a
session-scoped `kb_files` record → `KnowledgeBaseFile` with `scope: "session"`).

---

## 10. Auth & security

- Hash passwords (bcrypt/argon2); sign/verify JWTs (`sub`, `exp`).
- Scope every session/KB/retrieval query by `user_id`; cross-user access → 404.
- Retrieval must filter by scope (`kb` + current `session` only) — never return
  another conversation's session chunks. The filter runs on the **Qdrant payload
  scope fields** (§9.2); since they're denormalized, payload updates on
  promote/reindex are part of the security boundary, not just bookkeeping.
- **Qdrant (and your app DB) are internal services** — bind them to the private
  network; never expose them to the browser or accept their URLs from client
  input. (Embeddings/reranking run in-process via FlagEmbedding, so there's no
  embedding service to expose.)
- Rate-limit `POST /auth/login` and the chat endpoint.
- Validate upload type/size before storing; store blobs in object storage; serve
  `Attachment.url` via signed URLs or an authenticated proxy.

---

## 11. File storage

- Persist uploads to object storage / a blob dir; keep `storage_key` on
  `kb_files`.
- Enforce a max upload size (413); recommend ~25–100 MB configurable. Remember
  the real inline-vs-ingest gate is **tokens after parse** (§6), not this byte
  cap.

---

## 12. Recommended stack & layout

- **Python + FastAPI** (strong SSE + RAG ecosystem) or **Node + Hono**.
- **Qdrant** for vectors (dense + sparse named vectors; §9.2) + an **app DB on
  SQLite now** (SQLAlchemy/SQLModel + Alembic + repository pattern), migratable
  to Postgres/MySQL later by config (§9.1).
- **BGE-M3** embeddings + **`bge-reranker-v2-m3`** reranker (Apache-2.0,
  Indonesian-capable), both via **FlagEmbedding** (`BGEM3FlagModel` +
  `FlagReranker`), running **in-process** with torch — no embedding/reranker
  server to host. Pinned, not swappable. **Device auto-detects** CUDA → MPS →
  CPU (`EMBED_DEVICE` override); fp16 only on CUDA — portable across Windows+CUDA,
  Mac M1, CPU. (Promote to a server like TEI/Infinity only if you outgrow
  in-process throughput.)
- **Chat LLM via any OpenAI-compatible endpoint** (vLLM / Ollama / TGI /
  llama-server / hosted) + a **manual tool-use loop** (§7). Configured default:
  **Qwen on llama-server** at `MODEL_BASE_URL=https://llama.sccic.org/v1`
  (`MODEL_NAME` set to the served model). Model-agnostic by design — swapping is
  a config change. `search_knowledge_base` is a normal client tool; scope is
  injected server-side when you execute it.

### 12.1 Model & tool-calling notes (model-agnostic)

- Write the loop against `/v1/chat/completions` (`messages` + `tools`; read
  `tool_calls`; reply with `role: "tool"` + `tool_call_id`). Swapping models is a
  base-URL + model-name change.
- Tool-calling **reliability** varies by model even though the wire format
  doesn't. Mitigate by (a) choosing models known to be good at tool use, and
  (b) keeping constrained/grammar-guided JSON as a fallback for weaker local
  models.
- **(Optional) LiteLLM** as a self-hosted proxy if you later want one config to
  juggle several backends and normalize tool-calling quirks. Not needed day one.

### 12.2 Tools & MCP convention (locked)

**v1 ships three tools**, all behind one **tool registry** (uniform contract):
`search_knowledge_base` (native), `execute_code` (native; §13 sandbox), and the
`satudata-garut` toolset over MCP. The SSE contract is already tool-agnostic
(`StepEvent.toolName`/`toolArgs`), so adding tools needs **no frontend change**
(exception: rich-output tools like artifacts/canvas, which add a new event type —
deferred).

**Tool registry contract.** Every tool — native or MCP — exposes `name`,
`description`, `parameters` (OpenAI `function` JSON schema), and
`execute(args, context)`. The server-side `context` carries what the model must
not control: `user_id`, `session_id`, repos/DB handles, embedder, and a
progress-emit callback. The loop asks the registry for enabled tools, passes
them to the model, and dispatches `tool_calls` by name — it never references a
specific tool. **Adding a tool changes the registry, not the loop.**

**MCP integration (the `MCPManager`).** External tools are added by running an
MCP client in the backend — **never** the vendor-specific Messages-API connector.

- **Transport: `streamable-http`.** MCP servers run as long-lived HTTP services;
  the backend connects to their `/mcp` endpoint. Avoid stdio server-side (no
  subprocess babysitting in API workers). `satudata-mcp` already supports this
  via `SATUDATA_MCP_TRANSPORT=streamable-http`.
- **Config-driven.** Each server is a config entry — adding one is config, not
  code:
  ```yaml
  mcp_servers:
    - name: satudata-garut
      transport: streamable-http
      url: http://satudata-mcp:8800/mcp
      auth: { type: none }          # or { type: bearer, token_env: VAR }
      enabled: true
      allowed_tools: []             # empty = all
  ```
- **At startup**, `MCPManager` connects each enabled server, calls `list_tools()`,
  and wraps every tool into the registry, **namespaced by server**
  (`satudata-garut.search_datasets`). Credentials come from the server-side
  context (env), never exposed to the model.
- **Failure isolation.** A server down / timing out / auth-failing is caught and
  returned as a tool-result error (or its tools dropped for the turn) — never
  crashes the loop. One bad source can't take down the chat.
- **House convention — large payloads bypass the model.** Tools return small
  results inline; large results return a **`download_url`** for the
  code-execution sandbox (§13) to fetch directly (zero token cost). This is the
  `satudata get_dataset_data` pattern; all future sources follow it. The
  download fileserver host/port must be reachable from the **sandbox** network.
- **Context bloat.** With many tools, withhold full definitions from context and
  load per-turn (tool-search / deferred loading). Not needed at 2–3 servers;
  the registry is built so it can be switched on later.

`search_knowledge_base` stays a **hand-written** native tool (never MCP-ified) so
it keeps server-side scope injection. Future sources and the social scraper are
config-adds (the scraper additionally needs `auth` + likely an async/job pattern
for long scrapes).

```
backend/
├── app/
│   ├── auth/          # login, me, logout, jwt, hashing
│   ├── sessions/      # CRUD + messages + session files + promote
│   ├── chat/          # SSE endpoint + manual tool-use loop + event mapping
│   ├── kb/            # upload, list, reindex, tags, delete
│   ├── rag/           # ingestion worker, chunking, FlagEmbedding (BGE-M3, in-process),
│   │                  # Qdrant client, hybrid scoped retrieval (the tool impl)
│   ├── tools/         # registry + contract; builtin/ (search_kb, execute_code),
│   │                  # mcp/ (MCPManager: connect, list, wrap, namespace)
│   ├── models/        # app-DB models
│   └── schemas/       # request/response DTOs (camelCase out)
├── workers/           # ingestion queue consumer
├── migrations/
└── tests/
```

---

## 13. Code-execution sandbox (`execute_code`)

The runtime behind the `execute_code` tool. It exists because MCP tools (satudata
now, the scraper later) return large results as a **`download_url`** the model
can't read directly — the sandbox fetches and processes them. It's also the
producer for future artifact/canvas output (charts/files).

**Deployment (fits the locked topology).** The app runs on **Windows + CUDA**
(FlagEmbedding on GPU, Surya). Qdrant and the sandbox run in a **CPU Linux VM** via
Docker (same engine, two isolation domains). The sandbox is a small
**code-exec service** in that VM; the app reaches it over HTTP.

**Isolation = two walls + network (no Firecracker/E2B; no nested virt).** The
Hyper-V **VM is the hardware-isolation boundary**; ephemeral **containers**
inside it add per-session reset + a second wall. Microsandbox/E2B/Firecracker are
deliberately **not** used — they need KVM, i.e. nested virt inside Hyper-V
(fragile/slow). The four walls:
- **Filesystem** — container scratch dir only; no host FS.
- **Privilege** — non-root user; no escalation.
- **Resources (cgroups)** — mem/CPU caps + wall-clock timeout (e.g. 2 GB / 1 CPU
  / 30 s).
- **Network (the critical wall)** — sandbox containers sit on an isolated Docker
  network; egress **allowlist**: may reach the MCP fileserver(s) + public
  internet, **never** Qdrant / app DB / app host. If code ever escapes the
  container, it lands on the throwaway GPU-less VM with no secrets — the VM is
  the backstop.

**Four pieces:**
1. **Image (`Dockerfile`)** — `python:3.12-slim`, non-root `sandbox` user,
   `/work` scratch, preinstalled `pandas/numpy/matplotlib/httpx/ipython`, runs the
   in-container runner.
2. **Runner (in container)** — a tiny HTTP `/run` endpoint holding **one
   persistent `IPython.InteractiveShell`** so globals (a loaded dataframe) survive
   across calls; captures stdout, errors, and matplotlib figures (base64 PNG →
   artifacts).
3. **Code-exec service (in VM)** — owns container lifecycle via `docker-py`: one
   container **per conversation**, created with the network/mem/cpu/user limits,
   `POST /sessions/:id/execute` routes code to that container; idle-TTL reaper;
   `DELETE /sessions/:id` on session delete. The model never touches Docker.
4. **Network rule** — `docker network create sandbox_net` + iptables: drop
   internal, allow fileserver + internet.

**Tool binding.** `execute_code(args, context)` POSTs to the code-exec service
with the session from `context` (injected, never model-supplied); returns
`{ stdout, error, artifacts }`.

**Lifecycle ties to retention (§8.2).** Per-conversation container, idle timeout,
**destroyed on session delete** — same trigger that purges the session's Qdrant
points.

**End-to-end:** model calls `satudata-garut.get_dataset_data` → `download_url` →
`execute_code` (`pd.read_csv(url); df.describe()`) → service routes to the
conversation's container → container fetches (allowed egress) + runs pandas →
returns summary → model answers. Follow-ups reuse the same live `df`.

---

## 14. Seed data (for demo parity)

Source: constants in [`src/lib/mock.ts`](../src/lib/mock.ts).

- **Demo user:** `demo@example.com`, `displayName: "Alex Demo"`.
- **5 sessions** with sample messages (RAG, Python async, quarterly report w/
  attachment, DB optimization, welcome) — staggered timestamps so date-grouping
  shows.
- **6 KB files** (`scope: kb`) with varied state: `company-handbook.pdf` (ready,
  142, `hr,policy`), `api-documentation.md` (ready, 89, `engineering,api`),
  `quarterly-report-q1.pdf` (ready, 67, `finance,reports`),
  `product-roadmap.docx` (indexing, 0, `product,planning`),
  `research-paper.pdf` (ready, 203, `research,ml`), `meeting-notes.txt` (error,
  0, `meetings`).

---

## 15. Acceptance checklist

- [ ] Auth: login issues a JWT; `me` validates it; bad token → 401.
- [ ] Sessions CRUD works; list sorted `updatedAt desc`; delete cascades messages
      **and** session-scoped files/chunks.
- [ ] `GET /sessions/:id/messages` is chronological; user-message attachments
      include both inline and `ingested: true` files.
- [ ] **Chat is agentic:** `POST /sessions/:id/chat` runs a tool-use loop and
      emits `calling_tool` events **per actual `search_knowledge_base` call** —
      zero for trivial messages, several for compound ones — each with a unique
      `StepEvent.id`. Event JSON matches `src/types/chat.ts`.
- [ ] **Scope is server-injected:** retrieval searches `kb` + current `session`
      only; cross-session/-user chunks never leak. `toolArgs.scope` reflects
      whether the conversation has session files.
- [ ] **Ingress is token-based:** small attachments inline; large ones ingest
      session-scoped; the byte threshold is not the source of truth.
- [ ] **Scanned / image-only PDFs** are detected (thin text layer) and routed
      through **Surya OCR → text**, then chunked/embedded like any text — there
      is no multimodal embedding branch (BGE-M3 is text-only). Standalone images
      are chat-only via Qwen vision, never KB-ingested.
- [ ] **Attachment bytes upload at send:** `POST /sessions/:id/attachments`
      (multipart → SSE) stores blobs, **re-decides inline vs ingest by token
      count** (returns authoritative `ingested`), streams `chunk_progress` +
      `attachment_resolved`; the chat turn starts only **after** that stream
      closes. The frontend no longer discards `File` blobs.
- [ ] **KB upload is multipart → SSE:** streams `chunk_progress` then
      `file_resolved` for the active upload. **Reindex / pre-existing `indexing`
      files** are observed by the KB list **polling while any file is `indexing`**
      (reindex returns a plain JSON `indexing` record, not SSE).
- [ ] **Auth cookie + topology:** refresh token is httpOnly/Secure/`SameSite=Lax`
      on a shared origin (reverse proxy in prod, Vite `/api` proxy in dev);
      frontend does refresh-on-load + `401 → refresh → retry`, all requests with
      `credentials: "include"`.
- [ ] **Tool loop is model-agnostic:** runs against an OpenAI-compatible
      endpoint; swapping model = base-URL/model-name change; no vendor SDK in the
      loop; results returned as `role: "tool"` + `tool_call_id`.
- [ ] **Loop is bounded:** text streams only on no-tool-call turns; parallel
      calls each carry a unique `StepEvent.id`; `MAX_TOOL_ITERATIONS` (default 6)
      forces a final answer when hit; `MAX_PARALLEL_TOOLS` (default 2) caps
      concurrency.
- [ ] **Retrieval is hybrid:** dense + sparse (BGE-M3 via FlagEmbedding,
      in-process) fused with RRF
      under the server-side scope filter; embedding model is pinned (1024-dim
      dense); reranker (when enabled) is `bge-reranker-v2-m3` via FlagEmbedding's
      `FlagReranker`.
- [ ] **DB is portable:** SQLite via SQLAlchemy + Alembic + repositories; `tags`
      stored as JSON/child table (not a native array); UUIDs as text; WAL on.
      Engine swap to Postgres/MySQL is a connection-string change.
- [ ] **Two stores stay consistent:** chunks live in Qdrant with denormalized
      scope payload; `kb_files` delete / session cascade removes the file's
      Qdrant points; promote (`session → kb`) and reindex update those payloads.
- [ ] **Sandbox is walled:** `execute_code` runs in an ephemeral container inside
      the isolated VM; non-root, mem/CPU/time-capped; egress allowlist reaches the
      MCP fileserver + internet but **never** Qdrant/app DB/app host; container is
      per-conversation and destroyed on session delete.
- [ ] Session files: ingestion produces `scope: session` files retrievable in
      that chat; `GET /sessions/:id/files` lists them; they do **not** appear in
      `GET /knowledge-base`; promote flips one to `scope: kb`.
- [ ] KB upload validates type/size, ingests, flips `indexing → ready` with a
      real `chunkCount`; reindex/tags/delete behave per §8.3.
- [ ] Errors use `{ message, code }` with correct status codes.
- [ ] `src/lib/api.ts` swapped to real `fetch`; the existing UI works end-to-end
      with no other frontend changes.