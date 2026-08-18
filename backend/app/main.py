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
    # Apply any pending Alembic migrations before wiring routes — no manual
    # `alembic upgrade head` on deploy is needed.  `command.upgrade()` calls
    # `logging.config.fileConfig(alembic.ini)` internally, which resets the root
    # logger + all handlers and silently drops uvicorn's access-log handler.
    # Save and restore the logging state around the migration so uvicorn's
    # access logs survive.
    import logging.config as lc

    from alembic.config import Config as AlembicConfig
    from alembic import command

    _saved = lc.fileConfig
    try:
        # `command.upgrade()` calls `logging.config.fileConfig(alembic.ini)`
        # which resets the root logger and drops uvicorn's access-log handler.
        # Substitute a no-op so our handlers survive.
        lc.fileConfig = lambda *a, **kw: None  # type: ignore[assignment]
        alembic_cfg = AlembicConfig("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    finally:
        lc.fileConfig = _saved

    # Admin bootstrap — idempotent, never crashes startup.
    try:
        _bootstrap_admin()
    except Exception:
        log.exception("Admin bootstrap failed — continuing without bootstrap.")

    # OCR (PDFOxide/PaddleOCR) loads onnxruntime dynamically — point it at the
    # installed dylib so scanned-PDF OCR works; degrades to native text if absent.
    from app.rag.ocr_models import ensure_ort_dylib

    if ensure_ort_dylib() is None:
        log.warning(
            "onnxruntime dylib not found — scanned-PDF OCR will fall back to "
            "native text (no crash). Install onnxruntime / set ORT_DYLIB_PATH."
        )

    # Ingest-job + chat-turn registries (detached, disconnect-surviving
    # background work) + the ingest reaper.
    from app.chat.client import get_model_client
    from app.chat.turns import ChatTurnRegistry
    from app.db import SessionLocal, get_session_factory
    from app.rag.embedder import get_embedder
    from app.rag.ingest_jobs import IngestJobRegistry, reap_stranded
    from app.rag.vectors import get_client

    app.state.ingest_jobs = IngestJobRegistry(
        session_factory=get_session_factory(),
        client=get_client(),
        embedder=get_embedder(),
        max_concurrent=settings.max_concurrent_ingests,
    )

    # Chat-turn registry (detached, disconnect-surviving chat turns) — see
    # app/chat/turns.py for the replay/resume design.
    app.state.chat_turns = ChatTurnRegistry(
        model=get_model_client(),
        client=get_client(),
        embedder=get_embedder(),
        session_factory=get_session_factory(),
        max_concurrent=settings.max_concurrent_chat_turns,
    )
    # Voice sidecar reachability (spec §5): `/api/voice/capabilities` reports
    # `stt: true` only when VOICE_SERVICE_URL is set AND /health answered, so the
    # mic button never renders against a sidecar that isn't running. Probed once
    # here rather than per request, and non-fatal by construction — probe_sidecar
    # swallows its own errors, and this try is the belt to that braces.
    from app.voice.routes import STT_READY_ATTR, TTS_READY_ATTR, probe_sidecar

    try:
        stt_ready, tts_ready = await probe_sidecar()
        setattr(app.state, STT_READY_ATTR, stt_ready)
        setattr(app.state, TTS_READY_ATTR, tts_ready)
    except Exception:
        log.exception("Voice sidecar probe raised — continuing with voice disabled.")
        setattr(app.state, STT_READY_ATTR, False)
        setattr(app.state, TTS_READY_ATTR, False)

    try:
        _rdb = SessionLocal()
        try:
            reaped = reap_stranded(_rdb)
            if reaped:
                log.info("Reaper: marked %d stranded 'indexing' file(s) as error.", reaped)
        finally:
            _rdb.close()
    except Exception:
        log.exception("Startup reaper failed — continuing.")

    yield

    await app.state.ingest_jobs.shutdown()
    await app.state.chat_turns.shutdown()


def _bootstrap_admin() -> None:
    """Ensure a bootstrapped admin user exists when ADMIN_EMAIL / ADMIN_PASSWORD are set.

    Idempotent: if the email already exists, just promote the user to admin.
    Safe to call multiple times (e.g. from tests).
    """
    from app.auth.security import hash_password
    from app.db import SessionLocal
    from app.models import User

    if not settings.admin_email or not settings.admin_password:
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == settings.admin_email.lower()).first()
        if user:
            if not user.is_admin:
                user.is_admin = True
                db.commit()
                log.info("Admin bootstrap: promoted %s to admin.", settings.admin_email)
            else:
                log.info("Admin bootstrap: %s is already admin, nothing to do.", settings.admin_email)
        else:
            user = User(
                email=settings.admin_email.lower(),
                display_name="Admin",
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
            )
            db.add(user)
            db.commit()
            log.info("Admin bootstrap: created admin user %s.", settings.admin_email)
    finally:
        db.close()


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
from app.sessions.artifacts import router as artifacts_router  # noqa: E402
from app.sessions.attachments import router as attachments_router  # noqa: E402
from app.kb.routes import router as kb_router  # noqa: E402
from app.chat.routes import router as chat_router  # noqa: E402
from app.admin.routes import router as admin_router  # noqa: E402
from app.admin.groups import router as admin_groups_router  # noqa: E402
from app.admin.mcp import router as admin_mcp_router  # noqa: E402
from app.groups.routes import router as groups_router  # noqa: E402
from app.voice.routes import router as voice_router  # noqa: E402

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(attachments_router, prefix="/api/sessions", tags=["attachments"])
app.include_router(artifacts_router, prefix="/api/sessions", tags=["artifacts"])
app.include_router(kb_router, prefix="/api/knowledge-base", tags=["kb"])
app.include_router(chat_router, prefix="/api/sessions", tags=["chat"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(admin_groups_router, prefix="/api/admin", tags=["admin"])
app.include_router(admin_mcp_router, prefix="/api/admin", tags=["admin"])
app.include_router(groups_router, prefix="/api/groups", tags=["groups"])
app.include_router(voice_router, prefix="/api/voice", tags=["voice"])

# SPA serving — opt-in; must be wired AFTER all /api routers (catch-all is last).
_mount_spa(app)
