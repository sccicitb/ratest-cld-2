"""FastAPI entrypoint.

Run locally:  uv run uvicorn app.main:app --reload --port 8000
Health check: GET /api/health

Routers are added per docs/BACKEND_SPEC.md as each area is implemented.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.mcp.config import load_mcp_config
from app.mcp.manager import MCPManager

log = logging.getLogger(__name__)


def _mount_spa(target: FastAPI) -> None:
    """Mount the built SPA onto *target* when ``settings.spa_dir`` is configured.

    Call this AFTER all /api routers so the catch-all is the last route and
    cannot shadow API endpoints.  No-op when ``spa_dir`` is unset or missing.

    The function is importable (e.g. from tests) to wire SPA serving onto a
    fresh app without running the production singleton.
    """
    spa_dir = settings.spa_dir
    if not spa_dir:
        return
    spa = Path(spa_dir)
    if not spa.exists():
        log.warning("SPA_DIR %s does not exist — SPA serving disabled", spa_dir)
        return

    # Serve compiled assets (JS, CSS, images) via StaticFiles before the catch-all
    # so directory hits are handled directly without a filesystem check in the route.
    assets = spa / "assets"
    if assets.is_dir():
        target.mount("/assets", StaticFiles(directory=str(assets)), name="spa-assets")

    index_html = str(spa / "index.html")

    # Catch-all — MUST be the last route registered on *target*.
    # Unknown /api/* paths must return JSON 404, never index.html.
    @target.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str) -> FileResponse:  # type: ignore[return]
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        # Serve any known root-level file (e.g. favicon.ico, robots.txt).
        candidate = spa / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        # Fall back to index.html for all client-side routes.
        return FileResponse(index_html)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    manager = MCPManager(load_mcp_config())
    try:
        tools = await manager.connect_all()
    except Exception:
        log.exception("MCP connect_all failed at startup — continuing with no MCP tools.")
        tools = []
    app.state.mcp_manager = manager
    app.state.mcp_tools = tools
    yield
    await manager.aclose()


app = FastAPI(title="RAG Chat API", version="0.1.0", lifespan=lifespan)

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
from app.sessions.attachments import router as attachments_router  # noqa: E402
from app.kb.routes import router as kb_router  # noqa: E402
from app.chat.routes import router as chat_router  # noqa: E402

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(attachments_router, prefix="/api/sessions", tags=["attachments"])
app.include_router(kb_router, prefix="/api/knowledge-base", tags=["kb"])
app.include_router(chat_router, prefix="/api/sessions", tags=["chat"])

# SPA serving — opt-in; must be wired AFTER all /api routers (catch-all is last).
_mount_spa(app)
