# RAG Chat — Backend Specification

This document specifies the backend that replaces the mock layer in
[`src/lib/mock.ts`](../src/lib/mock.ts). It is written to be handed directly to
a coding agent (Codex, Claude Code, etc.) as an implementation brief.

The frontend already exists and is fully functional against an in-memory mock.
**The contract below is exactly what the frontend expects** — match it and the
UI works unchanged except for the one integration file noted in §1.

> **Architecture in one paragraph.** Retrieval from the knowledge base is an
> **agentic tool call** (`search_knowledge_base`) the model decides to make —
> not a hardcoded pipeline. Files reach the model two ways: small ones go
> **inline** (straight into context); large ones are **ingested** into a vector
> store and reached via the retrieval tool. There is one vector store with two
> faces — a **write side** (ingestion: chunk → embed → store) and a **read
> side** (the retrieval tool) — and a **scope** dimension separating the
> persistent Knowledge Base (`kb`) from per-conversation files (`session`).

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
4. **New endpoints since the first draft** — session-scoped files
   (`GET /sessions/:id/files`) and promote-to-KB
   (`POST /sessions/:id/files/:fileId/promote`). The frontend calls these via
   `getSessionFiles` / `promoteSessionFile` in `api.ts`.

On a hard refresh the user is unauthenticated (token is memory-only) and routed
to `/login`; no refresh-token flow is required for parity.

---

## 2. Conventions

| Aspect | Rule |
|---|---|
| Base path | All routes under `/api`. |
| Auth | `Authorization: Bearer <jwt>` on every route except `POST /auth/login`. |
| Content type | JSON for requests/responses; SSE endpoints return `text/event-stream`; uploads use `multipart/form-data`. |
| Timestamps | ISO-8601 UTC strings (`createdAt`, `updatedAt`, `uploadDate`). |
| IDs | Opaque strings (UUID v4 recommended). |
| Casing | JSON keys are **camelCase** (frontend types are camelCase). |
| CORS | Allow the SPA origin + `Authorization`, `Content-Type`. |

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

---

## 4. Authentication endpoints

### `POST /api/auth/login`
Mock: `mockLogin` (accepts any credentials, 500 ms). Verify email + password
hash, issue a signed JWT (`sub = user.id`, ~24h expiry).
- **200:** `AuthResponse` · **401:** invalid credentials.

### `GET /api/auth/me`
Mock: `mockGetMe`. Validate the bearer token.
- **200:** `User` · **401:** missing/invalid/expired.

### `POST /api/auth/logout`
Mock: `mockLogout`. Stateless JWT → server-side no-op is fine.
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

`INLINE_TOKEN_BUDGET` is configurable (a few thousand to ~tens of thousands of
tokens). The frontend cannot count tokens, so its byte thresholds
([`utils.ts`](../src/lib/utils.ts): `routeChatAttachment`) are only a **crude
proxy + sanity ceiling** — never the source of truth. The backend re-decides.

### Image-bearing PDFs (important)

When a PDF's meaning lives in its **images** (scans, diagrams, tables-as-images
— "content-bearing"), text extraction alone silently drops the content. Detect
low-text / image-heavy PDFs and route them to the **multimodal ingestion
branch** (§8.1): render pages → embed page images (or OCR) → retrieve. Do **not**
inline a large content-bearing PDF as raw vision — page-images cost ~1.5k–4.8k
tokens *each*, which blows context and cost for anything beyond a few pages.

### Three outcomes the composer produces (frontend → backend)

| Outcome | Frontend signal | Backend action |
|---|---|---|
| **inline** | attachment in the message `attachments[]`, `ingested` absent/false | parse → include in the model's context for that turn |
| **ingest** | attachment in `attachments[]` with `ingested: true`, **and** the file is uploaded for session-scoped ingestion | run the ingestion pipeline scoped to the session (§8) |
| **reject** | not sent | n/a (frontend blocks unsupported types / past the hard ceiling) |

> The model never "ingests." Ingestion is event-driven (an upload), not a tool
> the model calls. Only **retrieval** is a tool.

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
        "fileSize": 240000, "url": "#", "ingested": false }
    ]
  }
  ```
  Inline attachments are placed into the model's context for this turn. Ingested
  attachments (`ingested: true`) are *not* inlined — they're retrieved via the
  tool (their bytes were uploaded separately for ingestion, §8).
- **Response:** `text/event-stream`, one JSON `StreamEvent` per `data:` line,
  blank line between events, stream closed after `done`:
  ```
  data: {"type":"step","step":"thinking","status":"active"}

  data: {"type":"token","content":"Based "}

  data: {"type":"done","messageId":"msg_abc"}
  ```

### The retrieval tool

Define one tool and let the model decide when to call it:

```json
{
  "name": "search_knowledge_base",
  "description": "Search the user's indexed documents. Call this whenever the answer may depend on the user's files, recent data, or anything not already in the conversation. May be called multiple times with refined queries.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Semantic search query" },
      "tags":  { "type": "array", "items": { "type": "string" } }
    },
    "required": ["query"]
  }
}
```

**Scope is injected server-side, not by the model.** When the model calls the
tool, the backend runs the search filtered to this user's `kb` files **plus**
this conversation's `session` files:

```sql
SELECT content, file_id, chunk_idx
FROM kb_chunks c JOIN kb_files f ON f.id = c.file_id
WHERE f.user_id = :user
  AND f.status = 'ready'
  AND (f.scope = 'kb' OR (f.scope = 'session' AND f.session_id = :session))
ORDER BY c.embedding <=> :query_embedding
LIMIT :k;
```

The model never sees a session id. (Optional future nicety: expose a
`scope: "all" | "kb" | "session"` hint on the tool so the model can deliberately
narrow to "the file I just uploaded"; default `"all"`. Not required for parity.)

### Pipeline → event mapping

Run the standard tool-use loop (SDK tool runner or a manual loop). Emit events as
the loop progresses — the number of `calling_tool` events is **dynamic** (0..n):

| Loop event | SSE event(s) |
|---|---|
| Planning / query understanding begins → ends | `step thinking active` → `complete` |
| Model returns a `search_knowledge_base` tool call | `step calling_tool active` with a **unique `id`**, `toolName: "search_knowledge_base"`, `toolArgs: { query, scope }` |
| You run the vector search for that call | (optionally) `step retrieving_context active` → `complete` |
| You return the tool result; that call resolves | `step calling_tool complete` (same `id`) |
| Model generates the answer | `step generating_response active`, then `token` per delta, then `complete` |
| Loop ends (`end_turn`) | `done` with the persisted assistant `messageId` |

A trivial message (e.g. "thanks") may produce **zero** tool calls — emit only
`thinking` → `generating_response` → `done`. A compound question may produce
**several** `calling_tool` pairs, each with its own `id`.

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

There is **one vector store**. It has a **write side** (ingestion) and a **read
side** (the retrieval tool); they meet at the store.

```
WRITE (ingestion, event-driven)        READ (model-driven)
upload → parse → chunk → embed         search_knowledge_base tool
        ↓                                       ↑
              [ vector store + kb_files ] ───────┘
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

1. **Extract** — per type: PDF (`pypdf`/`pdfplumber`), DOCX (`python-docx`),
   TXT/MD/CSV/JSON directly.
2. **Decide text vs multimodal** — for **content-bearing / image-heavy PDFs**
   (low extractable text relative to page count), use the **multimodal branch**:
   render each page to an image and embed the page images (and/or OCR), so the
   visual content is actually captured. Otherwise use text extraction.
3. **Chunk** — ~500–1000 tokens, ~10–15% overlap, split on structure.
4. **Embed** — one fixed embedding model for **both** indexing and querying
   (mismatch silently breaks retrieval); output dim must match the `vector(N)`
   column. Multimodal pages use a multimodal embedder.
5. **Store** — upsert chunks + metadata (file id, chunk index, text, tags,
   **scope**, **session_id** when scoped).
6. **Finalize** — `status: "ready"`, `chunkCount = <#chunks>`; on failure
   `status: "error"`, `chunkCount: 0`.

Optionally stream `chunk_progress` events during ingestion (the chat UI renders
them inline; the type already exists). Reindex re-runs this pipeline.

### 8.2 Scopes

- **`kb`** — uploaded via the Knowledge Base page; persistent; on the KB page;
  retrievable from every chat.
- **`session`** — ingested from a chat attachment too big to inline; retrievable
  **only** in that conversation; **not** on the KB page; deleted with the
  session (or by TTL). A chat attachment's *origin* sets this scope — it inherits
  `session` by default; the user may **promote** it to `kb`.

### 8.3 Knowledge Base endpoints (scope `kb`)

| Method · Path | Mock | Behavior |
|---|---|---|
| `GET /api/knowledge-base` | `mockGetKBFiles` | `KnowledgeBaseFile[]` (scope `kb` only), sorted by `uploadDate` **desc**; filters `search` / `status` / `tag` (AND) |
| `POST /api/knowledge-base/upload` | `mockUploadKBFile` | `multipart/form-data` (`file`); validate type (415) + size (413); create `indexing` record; run §8.1; returns the file |
| `POST /api/knowledge-base/:id/reindex` | `mockReindexKBFile` | set `indexing`, re-run §8.1, back to `ready` with new `chunkCount` |
| `PATCH /api/knowledge-base/:id/tags` | `mockUpdateFileTags` | body `{ tags }` (lowercase + de-dupe) |
| `DELETE /api/knowledge-base/:id` | `mockDeleteKBFile` | delete file, blob, and chunks |

Supported types mirror `SUPPORTED_FILE_TYPES`
([`utils.ts`](../src/lib/utils.ts)): `.pdf .md .txt .docx .doc .csv .json .png
.jpg .jpeg`.

### 8.4 Session-scoped file endpoints (scope `session`)

| Method · Path | Mock | Behavior |
|---|---|---|
| ingestion | `mockIngestChatFile` | invoked when the chat composer sends an `ingest`-routed attachment: run §8.1 with `scope = session`, `session_id = :id`. Optionally stream `chunk_progress`. |
| `GET /api/sessions/:id/files` | `mockGetSessionFiles` | `KnowledgeBaseFile[]` for this session (scope `session`); powers the **"This chat's files"** section of the Sources drawer |
| `POST /api/sessions/:id/files/:fileId/promote` | `mockPromoteSessionFile` | flip a session file to `scope = kb` (the "Save to Knowledge Base" action); returns the promoted `KnowledgeBaseFile` |

> The frontend currently has the promote endpoint wired in the data layer but no
> UI button yet — implement the endpoint regardless.

---

## 9. Persistence schema (reference: PostgreSQL + pgvector)

```sql
create extension if not exists vector;

create table users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  display_name text not null,
  avatar_url text,
  password_hash text not null,
  created_at timestamptz not null default now()
);

create table sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  title text not null default 'New Chat',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on sessions (user_id, updated_at desc);

create table messages (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions(id) on delete cascade,
  role text not null check (role in ('user','assistant','system')),
  content text not null,
  created_at timestamptz not null default now()
);
create index on messages (session_id, created_at);

create table attachments (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references messages(id) on delete cascade,
  file_name text not null,
  file_type text not null,
  file_size bigint not null,
  url text not null,
  thumbnail_url text,
  ingested boolean not null default false
);

create table kb_files (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  scope text not null default 'kb' check (scope in ('kb','session')),
  session_id uuid references sessions(id) on delete cascade,   -- set when scope='session'
  name text not null,
  size bigint not null,
  upload_date timestamptz not null default now(),
  chunk_count int not null default 0,
  status text not null check (status in ('indexing','ready','error')),
  tags text[] not null default '{}',
  storage_key text not null,
  modality text not null default 'text' check (modality in ('text','multimodal'))
);
create index on kb_files (user_id, scope, upload_date desc);
create index on kb_files (session_id);

create table kb_chunks (
  id uuid primary key default gen_random_uuid(),
  file_id uuid not null references kb_files(id) on delete cascade,
  chunk_idx int not null,
  content text not null,
  embedding vector(1536) not null   -- match your embedding model dims
);
create index on kb_chunks using ivfflat (embedding vector_cosine_ops);
```

Serialize rows to the camelCase models in §3 at the API boundary (a
session-scoped `kb_files` row → `KnowledgeBaseFile` with `scope: "session"`).

---

## 10. Auth & security

- Hash passwords (bcrypt/argon2); sign/verify JWTs (`sub`, `exp`).
- Scope every session/KB/retrieval query by `user_id`; cross-user access → 404.
- Retrieval must filter by scope (`kb` + current `session` only) — never return
  another conversation's session chunks.
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

- **Python + FastAPI** (strong SSE + RAG ecosystem) or **Node + Express/Hono**.
- **PostgreSQL + pgvector** (or Qdrant/Weaviate).
- An **LLM** for generation via the tool-use loop + an **embedding model** for
  retrieval (and a **multimodal embedder** for image-bearing PDFs). When building
  on Claude, run the tool loop on the latest Claude model (`claude-opus-4-8`) and
  use the SDK's tool runner (or a manual loop) — `search_knowledge_base` is a
  normal user-defined tool; scope is injected server-side when you execute it.

```
backend/
├── app/
│   ├── auth/          # login, me, logout, jwt, hashing
│   ├── sessions/      # CRUD + messages + session files + promote
│   ├── chat/          # SSE endpoint + tool-use loop + event mapping
│   ├── kb/            # upload, list, reindex, tags, delete
│   ├── rag/           # ingestion worker, chunking, embeddings (+ multimodal),
│   │                  # vector store, scoped retrieval (the tool impl)
│   ├── models/        # ORM models
│   └── schemas/       # request/response DTOs (camelCase out)
├── workers/           # ingestion queue consumer
├── migrations/
└── tests/
```

---

## 13. Seed data (for demo parity)

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

## 14. Acceptance checklist

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
- [ ] **Image-bearing PDFs** use the multimodal ingestion branch (content isn't
      silently dropped to a thin text layer).
- [ ] Session files: ingestion produces `scope: session` files retrievable in
      that chat; `GET /sessions/:id/files` lists them; they do **not** appear in
      `GET /knowledge-base`; promote flips one to `scope: kb`.
- [ ] KB upload validates type/size, ingests, flips `indexing → ready` with a
      real `chunkCount`; reindex/tags/delete behave per §8.3.
- [ ] Errors use `{ message, code }` with correct status codes.
- [ ] `src/lib/api.ts` swapped to real `fetch`; the existing UI works end-to-end
      with no other frontend changes.
