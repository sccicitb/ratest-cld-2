"""Code-exec service — FastAPI entrypoint.

Owns per-conversation container lifecycle via ``ContainerManager``.
Runs standalone; wired into the app tool registry in Stage 10.

Run locally (after building the runner image):
    uv run uvicorn sandbox.service.main:app --port 8001

Endpoints:
    POST /sessions/{session_id}/execute   — run code, return stdout/error/artifacts
    DELETE /sessions/{session_id}         — remove container (call on session delete)
    GET /health                           — liveness probe
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .containers import ContainerManager

log = logging.getLogger(__name__)

_manager: ContainerManager | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    global _manager
    _manager = ContainerManager()
    _manager.ensure_network()

    reaper = asyncio.create_task(_reaper_loop())
    try:
        yield
    finally:
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper
        if _manager is not None:
            _manager.remove_all()


async def _reaper_loop() -> None:
    """Background task: evict idle containers every 60 s."""
    while True:
        await asyncio.sleep(60)
        try:
            if _manager is not None:
                _manager.reap_idle()
        except Exception:
            log.exception("Error in idle-container reaper")


app = FastAPI(title="rag-sandbox-service", lifespan=lifespan)


def _get_manager() -> ContainerManager:
    if _manager is None:
        raise RuntimeError("ContainerManager not initialised — lifespan not started")
    return _manager


class ExecuteRequest(BaseModel):
    code: str


@app.post("/sessions/{session_id}/execute")
def execute(session_id: str, req: ExecuteRequest) -> dict:
    """Execute *code* in the session's persistent sandbox container."""
    try:
        return _get_manager().execute(session_id, req.code)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    """Remove the sandbox container for *session_id*."""
    _get_manager().remove(session_id)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
