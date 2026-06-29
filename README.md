# RAG Chat

A self-hosted Retrieval-Augmented Generation chat app — React SPA + FastAPI
backend, agentic tool-calling retrieval, a managed knowledge base, and
session-scoped ingestion of large chat attachments.

## Monorepo layout

```
ratest-cld-2/
├── frontend/            # React 19 + React Router v8 SPA (TypeScript)
├── backend/             # FastAPI + Qdrant + FlagEmbedding (Python)
│   └── sandbox/         # code-exec sandbox (§13): runner image + docker-py service
├── docs/
│   └── BACKEND_SPEC.md  # the contract between the two — LOCKED for v1
├── docker-compose.yml   # dev infra (Qdrant)
└── README.md
```

The **frontend** (React SPA) is wired to the real **backend** (FastAPI), which
implements [`docs/BACKEND_SPEC.md`](docs/BACKEND_SPEC.md). The full v1 is
implemented — agentic retrieval, KB ingestion (+ OCR), the SSE chat tool-loop,
chat attachments, **MCP external tools**, and the **`execute_code` sandbox**.
Remaining work is production deployment.

## Run it

The full stack is **four services** (Qdrant, the code-exec sandbox service, the
backend, and the frontend) plus an optional MCP server. The authoritative,
step-by-step runbook lives in **[`backend/README.md`](backend/README.md)** — start
there. Quick version:

```bash
# infra + sandbox image (once)
docker compose up -d qdrant
cd backend && docker build -t rag-sandbox sandbox/runner && uv run python -m app.seed

# then, each in its own terminal:
uv run uvicorn sandbox.service.main:app --port 8001                  # sandbox service
COOKIE_SECURE=false uv run uvicorn app.main:app --port 8000          # backend
cd ../frontend && npm install && npm run dev                        # http://localhost:5173
```
Sign in with **demo@example.com / demo1234**.

## Architecture at a glance

- **Retrieval is a tool** (`search_knowledge_base`) the model chooses to call —
  not a hardcoded pipeline. Scope (`kb` + current `session`) is injected
  server-side; the model never sees a session id.
- **Files reach the model two ways:** small ones **inline** (into context),
  large ones are **ingested** (chunk → embed → Qdrant) and reached via the tool.
  The decision is **token-based**, made server-side after parsing.
- **One vector store, two scopes:** the persistent Knowledge Base (`kb`) and
  per-conversation files (`session`).
- **Three agent tools:** `search_knowledge_base` (native, scoped retrieval),
  external tools over **MCP** (e.g. satudata), and **`execute_code`** (a
  sandboxed Python runtime that fetches/analyzes large tool results). Adding a
  tool changes the registry, not the loop.
- **Self-hosted, model-agnostic:** the chat model is any OpenAI-compatible
  endpoint; embeddings/rerank run in-process (FlagEmbedding / BGE-M3).

Full design, endpoint contracts, SSE event shapes, schema, and the acceptance
checklist live in [`docs/BACKEND_SPEC.md`](docs/BACKEND_SPEC.md).
