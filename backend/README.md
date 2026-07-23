# RAG Chat — Backend

Self-hosted RAG chat API. The authoritative design is
[`../docs/BACKEND_SPEC.md`](../docs/BACKEND_SPEC.md) (status: **LOCKED for v1**) —
this README is just how to run it.

## Stack

| Concern | Choice |
|---|---|
| API | FastAPI (async SSE) |
| App data | SQLite now → Postgres/MySQL later (SQLAlchemy + Alembic + repositories) |
| Vectors | Qdrant (dense + sparse named vectors) |
| Embeddings / rerank | BGE-M3 + bge-reranker-v2-m3, **in-process** via FlagEmbedding (torch/GPU) |
| Chat LLM | any OpenAI-compatible endpoint (`MODEL_BASE_URL`) — default Qwen on llama-server |
| Extraction | PDFOxide (PaddleOCR-v4 ONNX for PDF text + OCR; PyMuPDF pre-pass for malformed) |

## Prerequisites

- **Python ≥ 3.10** and [`uv`](https://docs.astral.sh/uv/)
- **Docker** (runs Qdrant + the code-exec sandbox containers)
- **Node ≥ 22.22** (for the frontend)
- An **OpenAI-compatible chat endpoint** reachable at `MODEL_BASE_URL`
- *(optional)* an **MCP server** (e.g. `satudata-mcp`) for external tools
  (§12.2) — the app works fine without it

## Setup (once)

```bash
cd backend
cp .env.example .env          # then edit MODEL_BASE_URL etc.
# Install all extras (foundation + retrieval + chat + ingestion + MCP + sandbox):
uv sync --extra dev --extra rag --extra llm --extra ingest --extra mcp --extra sandbox

# Build the sandbox container image (used by execute_code, §13):
docker build -t rag-sandbox sandbox/runner

# Seed the demo user (demo@example.com / demo1234):
uv run python -m app.seed
```

> If `uv run` reports `ModuleNotFoundError` because of a stray `VIRTUAL_ENV`,
> prefix commands with `env -u VIRTUAL_ENV`.

## Run the full stack

The app talks to four services over HTTP. **Qdrant**, the **code-exec service**,
the **backend**, and the **frontend** are needed for the full experience; the
MCP server is optional. Start each in its own terminal (from the repo root unless
noted):

| # | Service | Command | Needed for |
|---|---------|---------|------------|
| 1 | **Qdrant** (vectors) | `docker compose up -d qdrant` | KB / retrieval |
| 2 | **Code-exec service** (sandbox) | `cd backend && uv run uvicorn sandbox.service.main:app --port 8001` | `execute_code` (§13) |
| 3 | **Backend** (API) | `cd backend && COOKIE_SECURE=false uv run uvicorn app.main:app --port 8000` | everything |
| 4 | **Frontend** (SPA) | `cd frontend && npm install && npm run dev` | the browser UI |
| 5 | *(optional)* **MCP server** | run your `satudata-mcp` on `:8800`, then set `enabled: true` + `url: http://localhost:8800/mcp` in `backend/mcp.yaml` | satudata tools (§12.2) |

Then open **http://localhost:5173** and sign in with **demo@example.com /
demo1234**.

- `COOKIE_SECURE=false` lets the auth refresh cookie work over plain http in dev.
- The frontend's Vite server proxies `/api` → `:8000`, so the browser sees one
  origin (keeps the auth cookie `SameSite=Lax`; §2/§4 of the spec).
- Health check: `curl http://localhost:8000/api/health` → `{"status":"ok"}`.
- If you skip #2, `execute_code` returns "code sandbox unavailable" (graceful) —
  start it to run code. If you skip #5, MCP tools simply aren't offered.

### What to try in the browser
- **Chat / retrieval:** upload a doc on the Knowledge Base page, then ask about
  it → the model calls `search_knowledge_base`.
- **Attachments:** drag a small file into the composer (inlined into context) or
  a large one (session-ingested, shown under "Sources").
- **Code execution:** *"Use code to compute the mean and std of [10,20,30,40,50]"*
  → the model calls `execute_code` and runs it in the sandbox.
- **MCP (if #5 up):** *"Search the Garut open-data catalog for population datasets."*

> Note: `execute_code` can generate charts, but rich/artifact output isn't
> rendered in the UI yet (deferred per spec) — the model will mention a chart
> without showing the image.

## Layout

```
backend/
├── app/
│   ├── main.py        # FastAPI app + health + CORS
│   ├── config.py      # settings (all spec knobs)
│   ├── auth/          # login, refresh, me, logout (§4)
│   ├── sessions/      # CRUD + messages + attachments + session files (§5/§6.1/§8.4)
│   ├── chat/          # SSE tool-use loop + event mapping (§7)
│   ├── kb/            # upload/list/reindex/tags/delete (§8.3)
│   ├── rag/           # ingestion worker, FlagEmbedding, Qdrant, scoped retrieval (§8)
│   ├── tools/         # tool registry + builtin (search_kb, execute_code) + mcp (§12.2)
│   ├── mcp/           # MCP config loader + MCPManager (connect/list/wrap/namespace)
│   ├── models/        # SQLAlchemy models
│   └── schemas/       # request/response DTOs (camelCase out)
├── sandbox/           # code-exec sandbox (§13): runner image + docker-py service
├── scripts/           # smoke scripts (smoke_mcp.py, smoke_execute_code.py)
├── mcp.yaml           # MCP server registry (§12.2) — satudata disabled by default
└── tests/
```

## Implementation status

Feature-complete for v1: auth, sessions, KB ingestion (+ PDFOxide OCR), agentic
retrieval, the SSE tool-use chat loop, chat attachments, **MCP external tools**
(§12.2), and the **`execute_code` sandbox** (§13) are all implemented and tested.
The frontend is wired to the real backend. **Remaining:** production deployment
(reverse proxy, prod compose, the sandbox's prod iptables egress rules) — see the
§15 acceptance checklist in the spec.
