# RAG Chat

A self-hosted Retrieval-Augmented Generation chat app — React SPA + FastAPI
backend, agentic tool-calling retrieval, a managed knowledge base, and
session-scoped ingestion of large chat attachments.

## Monorepo layout

```
ratest-cld-2/
├── frontend/            # React 19 + React Router v8 SPA (TypeScript)
├── backend/             # FastAPI + Qdrant + FastEmbed (Python)
├── docs/
│   └── BACKEND_SPEC.md  # the contract between the two — LOCKED for v1
├── docker-compose.yml   # dev infra (Qdrant)
└── README.md
```

The **frontend** is complete and runs today against an in-memory mock
(`frontend/src/lib/mock.ts`). The **backend** implements
[`docs/BACKEND_SPEC.md`](docs/BACKEND_SPEC.md); when it's live, the frontend's
`api.ts` swaps its mock delegates for real `fetch` calls (plus the file-upload,
send-sequencing, and auth changes the spec enumerates in §1).

## Run the frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173  (Node ≥ 22.22)
```
Sign in with any credentials (mock auth). See [`frontend/README.md`](frontend/README.md).

## Run the backend

```bash
docker compose up -d qdrant     # vector store
cd backend
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload --port 8000
```
See [`backend/README.md`](backend/README.md).

## Architecture at a glance

- **Retrieval is a tool** (`search_knowledge_base`) the model chooses to call —
  not a hardcoded pipeline. Scope (`kb` + current `session`) is injected
  server-side; the model never sees a session id.
- **Files reach the model two ways:** small ones **inline** (into context),
  large ones are **ingested** (chunk → embed → Qdrant) and reached via the tool.
  The decision is **token-based**, made server-side after parsing.
- **One vector store, two scopes:** the persistent Knowledge Base (`kb`) and
  per-conversation files (`session`).
- **Self-hosted, model-agnostic:** the chat model is any OpenAI-compatible
  endpoint; embeddings/rerank run in-process (FastEmbed / BGE-M3).

Full design, endpoint contracts, SSE event shapes, schema, and the acceptance
checklist live in [`docs/BACKEND_SPEC.md`](docs/BACKEND_SPEC.md).
