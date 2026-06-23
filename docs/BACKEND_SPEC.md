# RAG Chat — Backend Specification

This document specifies the backend that replaces the mock layer in
[`src/lib/mock.ts`](../src/lib/mock.ts). It is written to be handed directly to
a coding agent (Codex, Claude Code, etc.) as an implementation brief.

The frontend already exists and is fully functional against an in-memory mock.
**The contract below is exactly what the frontend expects** — match it and the
UI works unchanged except for the one integration file noted in §1.

---

## 1. Frontend integration contract

The frontend talks to the backend through a single file:
[`src/lib/api.ts`](../src/lib/api.ts). Every function there currently delegates
to `mock.*`. To go live, replace each delegate with a `fetch` to the endpoints
in §5. Nothing else in the app needs to change, with two specifics:

1. **Base URL** comes from `import.meta.env.VITE_API_BASE_URL`
   (e.g. `http://localhost:8000/api`).
2. **Auth token** is held in memory by the auth store
   ([`src/lib/auth.ts`](../src/lib/auth.ts) → `getToken()`). Send it as
   `Authorization: Bearer <accessToken>` on every authenticated request.
3. **Streaming** — `streamChat()` must return an
   `AsyncGenerator<StreamEvent>`. It will fetch the SSE endpoint (§6) and yield
   parsed `StreamEvent` objects. The event object shapes must match
   [`src/types/chat.ts`](../src/types/chat.ts) verbatim.

> The frontend never persists the token to `localStorage`; on a hard refresh the
> user is unauthenticated and routed to `/login`. The backend does not need a
> refresh-token flow for parity, though it may add one (see §9).

---

## 2. Conventions

| Aspect | Rule |
|---|---|
| Base path | All routes are under `/api` (configurable). |
| Auth | `Authorization: Bearer <jwt>` on every route except `POST /auth/login`. |
| Content type | `application/json` for requests/responses; SSE endpoints return `text/event-stream`; uploads use `multipart/form-data`. |
| Timestamps | ISO-8601 UTC strings, e.g. `2026-06-21T09:30:00.000Z`. Field names: `createdAt`, `updatedAt`, `uploadDate`. |
| IDs | Opaque strings (UUID v4 recommended). The frontend treats them as strings only. |
| CORS | Allow the SPA origin; allow `Authorization`, `Content-Type`; expose nothing special. |
| Casing | JSON keys are **camelCase** (the frontend types are camelCase). |

### Error format

Non-2xx responses return JSON. The frontend's `ApiError`
([`src/lib/api.ts`](../src/lib/api.ts)) carries `status`, `message`, `code`.

```json
{ "message": "Session not found", "code": "not_found" }
```

| Status | When |
|---|---|
| 400 | Validation error (bad body / params). |
| 401 | Missing/invalid/expired token. |
| 403 | Authenticated but not allowed to touch the resource. |
| 404 | Resource does not exist (or not owned by the caller). |
| 409 | Conflict (e.g. duplicate). |
| 413 | Upload exceeds max size. |
| 415 | Unsupported file type. |
| 422 | Semantic validation failure. |
| 500 | Unexpected. |

---

## 3. Data models

These mirror [`src/types/api.ts`](../src/types/api.ts),
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
| Field | Type |
|---|---|
| `accessToken` | string (JWT) |
| `user` | User |

### Session
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `title` | string | defaults to `"New Chat"` on creation |
| `createdAt` | string (ISO) | |
| `updatedAt` | string (ISO) | bumped on new message / rename |

### Message
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `sessionId` | string | |
| `role` | `"user" \| "assistant" \| "system"` | |
| `content` | string | Markdown for assistant messages |
| `attachments` | Attachment[]? | present on user messages with files |
| `createdAt` | string (ISO) | |

### Attachment
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `fileName` | string | |
| `fileType` | string | MIME type |
| `fileSize` | number | bytes |
| `url` | string | downloadable/served URL |
| `thumbnailUrl` | string? | optional (images) |

### KnowledgeBaseFile
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `name` | string | original filename |
| `size` | number | bytes |
| `uploadDate` | string (ISO) | |
| `chunkCount` | number | `0` while indexing/error, else count of indexed chunks |
| `status` | `"indexing" \| "ready" \| "error"` | |
| `tags` | string[] | lowercase, de-duplicated |

---

## 4. Authentication endpoints

### `POST /api/auth/login`
Mock ref: `mockLogin` (accepts any credentials, 500 ms).

- **Body:** `{ "email": string, "password": string }`
- **200:** `AuthResponse`
- **401:** invalid credentials (real backend; the demo seed user should still log in — see §12).

Real behavior: verify email + password hash, issue a signed JWT
(`sub = user.id`, reasonable expiry, e.g. 24h).

### `GET /api/auth/me`
Mock ref: `mockGetMe` (200 ms; 401 on bad token).

- **200:** `User`
- **401:** missing/invalid/expired token.

### `POST /api/auth/logout`
Mock ref: `mockLogout` (200 ms, no-op).

- **200:** empty body. Stateless JWT → server-side no-op is acceptable; if using
  a denylist, revoke here.

---

## 5. Sessions & messages endpoints

All require auth and are **scoped to the authenticated user** (return 404 for
other users' sessions, never 403-leak existence).

### `GET /api/sessions`
Mock ref: `mockGetSessions` (400 ms). Sorted by `updatedAt` **descending**.
- **200:** `Session[]`

### `GET /api/sessions/:id`
Mock ref: `mockGetSession` (200 ms).
- **200:** `Session` · **404:** unknown id.

### `POST /api/sessions`
Mock ref: `mockCreateSession` (300 ms). Creates an empty session titled
`"New Chat"` with no messages.
- **201/200:** `Session`

### `PATCH /api/sessions/:id`
Mock ref: `mockRenameSession` (200 ms). Updates the title; bumps `updatedAt`.
- **Body:** `{ "title": string }`
- **200:** `Session` · **404:** unknown id.

### `DELETE /api/sessions/:id`
Mock ref: `mockDeleteSession` (200 ms). Cascade-deletes its messages.
- **204/200:** empty · **404:** unknown id.

### `GET /api/sessions/:id/messages`
Mock ref: `mockGetMessages` (300 ms). Chronological (oldest → newest).
- **200:** `Message[]`

---

## 6. Chat streaming (SSE)

### `POST /api/sessions/:id/chat`
Mock ref: `mockStreamChat`. This is the core endpoint. It is a **Server-Sent
Events** stream.

- **Request headers:** `Authorization`, `Accept: text/event-stream`.
- **Request body** (`application/json`):
  ```json
  {
    "message": "How does RAG work?",
    "attachments": [
      { "id": "att-1", "fileName": "report.pdf", "fileType": "application/pdf", "fileSize": 2340000, "url": "#" }
    ]
  }
  ```
  `attachments` is optional. (Alternatively accept `multipart/form-data` if you
  want raw file bytes uploaded with the turn; the current frontend only sends
  attachment metadata.)
- **Response:** `Content-Type: text/event-stream`, one event per line block:
  ```
  data: {"type":"step","step":"thinking","status":"active"}

  data: {"type":"token","content":"Based "}

  ...
  data: {"type":"done","messageId":"msg-abc"}
  ```
  Each `data:` line is a JSON-encoded `StreamEvent`. End the stream after `done`.

#### Server-side side effects (must match mock)
1. Persist the **user message** immediately (with attachments).
2. If the session title is still `"New Chat"`, auto-title it from the first user
   message (truncate to ~40 chars, append `…` if longer). Bump `updatedAt`.
3. Run the pipeline (below), streaming events.
4. Persist the **assistant message** with the full accumulated `content`.
5. Bump session `updatedAt` again. Emit `done` with the assistant `messageId`.

#### StreamEvent schema
From [`src/types/chat.ts`](../src/types/chat.ts) — discriminated union on `type`:

```ts
// step
{ type: "step", step: PipelineStep, status: "active" | "complete",
  toolName?: string, toolArgs?: Record<string, unknown> }
// token (incremental text)
{ type: "token", content: string }
// chunk_progress (used by ingestion stream, §7)
{ type: "chunk_progress", fileName: string, progress: number,
  chunkCount: number, total: number }
// done
{ type: "done", messageId: string }
// error
{ type: "error", message: string }

type PipelineStep =
  | "thinking" | "retrieving_context" | "calling_tool" | "generating_response";
```

#### Pipeline sequence (what to emit, and what it means in a real system)
The mock uses fixed delays; the real backend emits the **same events** driven by
real work. Emit `status:"active"` when a stage starts and `status:"complete"`
when it finishes.

| Order | Event | Real meaning |
|---|---|---|
| 1 | `step thinking active` → `complete` | Query understanding / planning (LLM or router). |
| 2 | `step retrieving_context active` → `complete` | Embed the query, similarity-search the vector store for top-k chunks. |
| 3 | `step calling_tool active` (`toolName:"search_knowledge_base"`, `toolArgs:{query}`) → `complete` | Any tool/function call. The mock always emits the KB search tool; real backends emit per actual tool invocations (0..n). |
| 4 | `step generating_response active` | LLM generation begins. |
| 5 | repeated `token` events | Stream the LLM's tokens/words as they arrive. The mock emits word-by-word (~30–50 ms); real backends forward provider tokens. |
| 6 | `step generating_response complete` | Generation finished. |
| 7 | `done` | Final; includes persisted `messageId`. |

On failure mid-stream, emit `{ "type": "error", "message": "<safe message>" }`
and close the stream (after persisting whatever is appropriate).

> Reference response content: the mock keys off keywords (`rag|embedding`,
> `python|async|code`, `report|finance`) to pick a templated Markdown answer,
> else a default. The real backend produces this from retrieval + the LLM; no
> need to replicate the templates.

---

## 7. Knowledge base endpoints

### `GET /api/knowledge-base`
Mock ref: `mockGetKBFiles` (400 ms). Sorted by `uploadDate` **descending**.
Supports optional query params (all combine with AND):

| Param | Effect |
|---|---|
| `search` | case-insensitive substring match on `name` |
| `status` | exact match on `status` (`ready` / `indexing` / `error`) |
| `tag` | files containing this tag |

- **200:** `KnowledgeBaseFile[]`

### `POST /api/knowledge-base/upload`
Mock ref: `mockUploadKBFile`. Accepts a file, stores it, kicks off ingestion.

- **Request:** `multipart/form-data` with a `file` field.
- **Validation:** reject unsupported extensions (415) and oversize (413).
  Supported types mirror `SUPPORTED_FILE_TYPES`
  ([`src/lib/utils.ts`](../src/lib/utils.ts)):
  `.pdf .md .txt .docx .doc .csv .json .png .jpg .jpeg`.
- **Behavior:** create the record with `status:"indexing"`, `chunkCount:0`, then
  run the ingestion pipeline (§7.1). Return once the record exists.
- **200/201:** `KnowledgeBaseFile`

Progress reporting: the mock simulates 0→100% upload then an indexing phase. For
a real backend you have two valid options:
- **Simple (matches current frontend):** return the file record; the frontend
  polls `GET /knowledge-base` (already invalidated by TanStack Query) and the
  status flips `indexing → ready` when ingestion completes.
- **Streaming (optional):** expose an ingestion SSE (below) and wire it into the
  upload UI. The event type already exists (`chunk_progress`).

### `POST /api/knowledge-base/:id/reindex`
Mock ref: `mockReindexKBFile` (200 ms, then ready after ~2 s). Sets
`status:"indexing"`, `chunkCount:0`, re-runs ingestion; on completion sets
`status:"ready"` with the new chunk count.
- **200:** `KnowledgeBaseFile` (immediately, in `indexing` state) · **404**.

### `PATCH /api/knowledge-base/:id/tags`
Mock ref: `mockUpdateFileTags` (200 ms). Replaces the tag set.
- **Body:** `{ "tags": string[] }` (lowercase + de-dupe server-side)
- **200:** `KnowledgeBaseFile` · **404**.

### `DELETE /api/knowledge-base/:id`
Mock ref: `mockDeleteKBFile` (200 ms). Deletes the file, its stored blob, and
its vector chunks.
- **204/200:** empty · **404**.

### `GET /api/knowledge-base/:id/ingest` (optional SSE)
Mock ref: `mockSimulateChunkProgress`. Streams `chunk_progress` events while a
file is being chunked + embedded:
```
data: {"type":"chunk_progress","fileName":"x.pdf","progress":40,"chunkCount":18,"total":45}
```
`progress` is 0–100; emit a terminal event at `progress:100` then close.

### 7.1 Ingestion pipeline (behind upload/reindex)
1. Extract text (PDF/DOCX/TXT/MD/CSV/JSON; OCR or caption images if desired).
2. Chunk (e.g. ~500–1000 tokens, overlap ~10–15%).
3. Embed each chunk with a fixed embedding model (**use the same model for
   indexing and querying**).
4. Upsert vectors + metadata (file id, chunk index, text, tags) into the vector
   store.
5. Update the file record: `status:"ready"`, `chunkCount = <#chunks>`. On
   failure: `status:"error"`, `chunkCount:0`.

---

## 8. Persistence schema (reference: PostgreSQL + pgvector)

```sql
create extension if not exists vector;

create table users (
  id            uuid primary key default gen_random_uuid(),
  email         text unique not null,
  display_name  text not null,
  avatar_url    text,
  password_hash text not null,
  created_at    timestamptz not null default now()
);

create table sessions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references users(id) on delete cascade,
  title      text not null default 'New Chat',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on sessions (user_id, updated_at desc);

create table messages (
  id         uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions(id) on delete cascade,
  role       text not null check (role in ('user','assistant','system')),
  content    text not null,
  created_at timestamptz not null default now()
);
create index on messages (session_id, created_at);

create table attachments (
  id            uuid primary key default gen_random_uuid(),
  message_id    uuid not null references messages(id) on delete cascade,
  file_name     text not null,
  file_type     text not null,
  file_size     bigint not null,
  url           text not null,
  thumbnail_url text
);

create table kb_files (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references users(id) on delete cascade,
  name        text not null,
  size        bigint not null,
  upload_date timestamptz not null default now(),
  chunk_count int not null default 0,
  status      text not null check (status in ('indexing','ready','error')),
  tags        text[] not null default '{}',
  storage_key text not null            -- pointer to the stored blob
);
create index on kb_files (user_id, upload_date desc);

create table kb_chunks (
  id        uuid primary key default gen_random_uuid(),
  file_id   uuid not null references kb_files(id) on delete cascade,
  chunk_idx int not null,
  content   text not null,
  embedding vector(1536) not null      -- match your embedding model dims
);
create index on kb_chunks using ivfflat (embedding vector_cosine_ops);
```

Serialize rows to the camelCase JSON models in §3 at the API boundary.

---

## 9. Auth & security

- Hash passwords with bcrypt/argon2; never store plaintext.
- Sign JWTs with a server secret; include `sub` (user id) and `exp`. Validate on
  every protected route.
- Scope every session/KB query by `user_id`. Return 404 (not 403) for resources
  the caller doesn't own, to avoid leaking existence.
- Rate-limit `POST /auth/login` and the chat endpoint.
- Validate upload type/size before storing. Store blobs outside the web root or
  in object storage (S3/GCS); serve `Attachment.url` via signed URLs or an
  authenticated proxy route.
- Set permissive-but-scoped CORS for the SPA origin.

---

## 10. File storage

- Persist uploaded files to object storage or a blob dir; keep `storage_key` on
  `kb_files`.
- `Attachment.url` should resolve to a downloadable URL (signed or proxied).
- Enforce a max upload size (return 413); recommend ~25 MB default, configurable.

---

## 11. Recommended stack & layout

Stack-agnostic, but a natural reference implementation:

- **Python + FastAPI** (great SSE support, RAG ecosystem) **or Node + Express/Hono**.
- **PostgreSQL + pgvector** for data and embeddings (or a dedicated vector DB
  such as Qdrant/Weaviate).
- **An LLM provider** for generation + an embedding model for retrieval. When
  building on Claude, default to the latest Claude models for generation.

```
backend/
├── app/
│   ├── main.py / index.ts        # bootstrap + CORS
│   ├── auth/                      # login, me, logout, jwt, hashing
│   ├── sessions/                  # CRUD + messages
│   ├── chat/                      # SSE endpoint + RAG pipeline + tools
│   ├── kb/                        # upload, list, reindex, tags, delete, ingest
│   ├── rag/                       # chunking, embeddings, vector store, retrieval
│   ├── models/                    # ORM models
│   └── schemas/                   # request/response DTOs (camelCase out)
├── migrations/
└── tests/
```

---

## 12. Seed data (for demo parity)

Optional but recommended so a fresh deploy mirrors the mock UX. Seed source:
the constants in [`src/lib/mock.ts`](../src/lib/mock.ts).

- **Demo user:** `email: demo@example.com`, `displayName: "Alex Demo"` (set any
  demo password; the frontend login pre-fills `demo@example.com`).
- **5 sessions** with sample messages: "How does RAG work?", "Python async
  patterns", "Quarterly report analysis" (with an attachment), "Database
  optimization tips", "Welcome to RAG Chat" — staggered timestamps (today,
  3h ago, yesterday, 5 days, 2 weeks) so date-grouping is exercised.
- **6 KB files** with varied state: `company-handbook.pdf` (ready, 142 chunks,
  `hr,policy`), `api-documentation.md` (ready, 89, `engineering,api`),
  `quarterly-report-q1.pdf` (ready, 67, `finance,reports`),
  `product-roadmap.docx` (indexing, 0, `product,planning`),
  `research-paper.pdf` (ready, 203, `research,ml`),
  `meeting-notes.txt` (error, 0, `meetings`).

---

## 13. Acceptance checklist

- [ ] `POST /auth/login` returns a JWT + user; `GET /auth/me` validates it; bad
      token → 401.
- [ ] Sessions CRUD works; list sorted by `updatedAt desc`; deletes cascade.
- [ ] `GET /sessions/:id/messages` returns chronological messages.
- [ ] `POST /sessions/:id/chat` streams SSE in the exact event order of §6,
      persists both messages, auto-titles `"New Chat"`, bumps `updatedAt`.
- [ ] StreamEvent JSON shapes match `src/types/chat.ts` exactly.
- [ ] KB list supports `search` / `status` / `tag` filters; sorted by
      `uploadDate desc`.
- [ ] Upload validates type/size, ingests, flips `indexing → ready` with a real
      `chunkCount`; failure → `error`.
- [ ] Reindex, tag update, delete behave per §7.
- [ ] All session/KB resources are user-scoped; cross-user access → 404.
- [ ] Errors use the `{ message, code }` shape with correct status codes.
- [ ] `src/lib/api.ts` swapped to real `fetch` calls and the existing UI works
      end-to-end with no other frontend changes.
