"""FastAPI entrypoint.

Run locally:  uv run uvicorn app.main:app --reload --port 8000
Health check: GET /api/health

Routers are added per docs/BACKEND_SPEC.md as each area is implemented.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

app = FastAPI(title="RAG Chat API", version="0.1.0")

from app.errors import register_error_handlers  # noqa: E402
register_error_handlers(app)

# In production the SPA and API share an origin behind a reverse proxy, so CORS
# is moot; in dev the Vite server proxies /api → here. These origins are a
# convenience for running split-origin during development (§2).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,  # required for the refresh cookie (§4)
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Routers (implemented per docs/BACKEND_SPEC.md) -------------------------
from app.auth.routes import router as auth_router  # noqa: E402
from app.sessions.routes import router as sessions_router  # noqa: E402
from app.kb.routes import router as kb_router  # noqa: E402
from app.chat.routes import router as chat_router  # noqa: E402

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(kb_router, prefix="/api/knowledge-base", tags=["kb"])
app.include_router(chat_router, prefix="/api/sessions", tags=["chat"])
