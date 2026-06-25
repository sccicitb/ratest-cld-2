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
| Embeddings / rerank | BGE-M3 + bge-reranker-v2-m3, **in-process** via FastEmbed |
| Chat LLM | any OpenAI-compatible endpoint (`MODEL_BASE_URL`) — default Qwen on llama-server |
| Extraction | PyMuPDF (text PDFs) + Surya OCR (scanned) |

## Prerequisites

- **Python ≥ 3.10** and [`uv`](https://docs.astral.sh/uv/)
- **Qdrant** running (the repo's root `docker-compose.yml` brings it up)
- An **OpenAI-compatible chat endpoint** reachable at `MODEL_BASE_URL`

## Setup

```bash
cd backend
cp .env.example .env                      # then edit MODEL_BASE_URL etc.
uv sync --extra dev                       # foundation (web + db + auth) + test deps
# later, per area:
#   uv sync --extra rag --extra llm       # Qdrant + FastEmbed + chat client
#   uv sync --extra ingest                # PyMuPDF / python-docx (§8.1)
```

Bring up Qdrant (from the repo root):

```bash
docker compose up -d qdrant
```

## Run

```bash
uv run uvicorn app.main:app --reload --port 8000
# health: curl http://localhost:8000/api/health  ->  {"status":"ok"}
```

In dev the frontend's Vite server proxies `/api` → `:8000`, so the browser sees
one origin (keeps the auth cookie `SameSite=Lax`; §2/§4 of the spec).

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
│   ├── rag/           # ingestion worker, FastEmbed, Qdrant, scoped retrieval (§8)
│   ├── tools/         # tool registry + builtin (search_kb, execute_code) + mcp (§12.2)
│   ├── models/        # SQLAlchemy models
│   └── schemas/       # request/response DTOs (camelCase out)
├── workers/           # ingestion queue consumer
├── migrations/        # Alembic
└── tests/
```

## Implementation status

Skeleton only — `GET /api/health` works; routers are stubbed in `main.py` and
built out per the spec. See the §15 acceptance checklist in the spec for the
definition of done.
