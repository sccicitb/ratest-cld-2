"""Voice service — STT sidecar (voice mode §1a).

Runs on the GPU host, separate from the backend, so engine dependencies can
churn without risking the backend's ability to start.

Run locally:
    cd backend/voice && uv run uvicorn voice.service.main:app --port 8002

Endpoints:
    POST /transcribe   — multipart audio -> text
    GET  /health       — liveness + what actually loaded
"""
from __future__ import annotations

import contextlib
import logging

from fastapi import Depends, FastAPI, Form, UploadFile
from fastapi.responses import JSONResponse

from .audio import decode_audio
from .config import settings
from .engines import Transcriber, build_transcriber

log = logging.getLogger(__name__)

_transcriber: Transcriber | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Load the model once, at startup -- never per request."""
    global _transcriber
    _transcriber = build_transcriber(settings)
    log.info(
        "voice: loaded %s / %s on %s",
        _transcriber.name, _transcriber.model, _transcriber.device,
    )
    yield
    _transcriber = None


app = FastAPI(title="Voice Service", version="0.1.0", lifespan=lifespan)


def get_transcriber() -> Transcriber:
    """Returns the transcriber lifespan() built into the module global.

    This alone is not the test seam: lifespan() calls build_transcriber()
    directly rather than through this dependency, so overriding it here
    would not stop a real engine from loading at TestClient startup. The
    real seam is patching build_transcriber before the app's lifespan runs
    (see tests/conftest.py's client fixture); the dependency_overrides entry
    on this function is kept alongside it only as a second, route-level guard.
    """
    assert _transcriber is not None, "transcriber not initialised"
    return _transcriber


@app.get("/health")
def health(t: Transcriber = Depends(get_transcriber)) -> dict:
    return {"status": "ok", "engine": t.name, "model": t.model, "device": t.device}


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile,
    language: str = Form(default=""),
    t: Transcriber = Depends(get_transcriber),
):
    raw = await audio.read()
    try:
        samples, duration = decode_audio(raw)
    except ValueError as exc:
        return JSONResponse(
            {"message": str(exc), "code": "audio_undecodable"}, status_code=400
        )

    lang = language or settings.language or None
    text = t.transcribe(samples, lang)
    return {
        "text": text,
        "language": lang or "auto",
        "durationMs": round(duration * 1000),
        "engine": t.name,
        "model": t.model,
    }
