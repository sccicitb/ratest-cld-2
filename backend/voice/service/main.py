"""Voice service — STT sidecar (voice mode §1a).

Runs on the GPU host, separate from the backend, so engine dependencies can
churn without risking the backend's ability to start.

Run locally:
    cd backend/voice && uv run uvicorn voice.service.main:app --app-dir .. --port 8002

`--app-dir ..` is not optional: the deps live in `backend/voice/.venv`, so uv has
to run from here, but the package root is `backend/` (this is `voice.service`,
and the tests import it by that name via pytest's `pythonpath = [".."]`).
Without it uvicorn exits with `ModuleNotFoundError: No module named 'voice'`.

Endpoints:
    POST /transcribe   — multipart audio -> text
    GET  /health       — liveness + what actually loaded
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import wave

import numpy as np
from fastapi import Body, Depends, FastAPI, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from .audio import decode_audio
from .config import settings
from .engines import Transcriber, build_transcriber
from .tts import DEFAULT_VOICE, Synthesizer, build_synthesizer

log = logging.getLogger(__name__)

_transcriber: Transcriber | None = None
_synthesizer: Synthesizer | None = None

# One in-flight transcription at a time, enforced explicitly.
#
# Two separate reasons, and both matter:
#
# 1. A single WhisperModel is NOT safe for concurrent transcribe() calls. Until
#    now that was satisfied by accident: the blocking call sat directly in an
#    `async def`, so the event loop could not start a second one. Moving the work
#    to a thread (below) removes the accident, so the mutual exclusion has to
#    become deliberate or we trade a hung /health for corrupt output.
# 2. It is the backpressure. The L40 is shared with llama-server; an
#    authenticated client looping uploads must queue, not multiply.
_engine_slot: asyncio.Semaphore | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Load the model once, at startup -- never per request."""
    global _transcriber, _engine_slot, _synthesizer
    _engine_slot = asyncio.Semaphore(1)
    _transcriber = build_transcriber(settings)
    log.info(
        "voice: loaded %s / %s on %s",
        _transcriber.name, _transcriber.model, _transcriber.device,
    )
    _synthesizer = build_synthesizer(settings)
    log.info("voice: loaded TTS %s / %s", _synthesizer.name, _synthesizer.model)
    yield
    _transcriber = None
    _engine_slot = None
    _synthesizer = None


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


def get_synthesizer() -> Synthesizer:
    """Mirror of get_transcriber -- see its docstring for why the real test
    seam is patching build_synthesizer, not overriding this."""
    assert _synthesizer is not None, "synthesizer not initialised"
    return _synthesizer


def get_engine_slot() -> asyncio.Semaphore:
    """The single-slot guard around the engine (see `_engine_slot`)."""
    assert _engine_slot is not None, "engine slot not initialised"
    return _engine_slot


@app.get("/health")
def health(
    t: Transcriber = Depends(get_transcriber),
    s: Synthesizer = Depends(get_synthesizer),
) -> dict:
    """Liveness. Deliberately does NOT wait on the engine slot.

    A busy GPU is not a dead service: if /health blocked behind a transcription,
    liveness monitoring (and NSSM) would read "down" during entirely normal work
    and restart the process mid-request.
    """
    return {
        "status": "ok",
        "engine": t.name,
        "model": t.model,
        "device": t.device,
        "tts": {"engine": s.name, "model": s.model, "voices": s.voices},
    }


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile,
    language: str = Form(default=""),
    t: Transcriber = Depends(get_transcriber),
    slot: asyncio.Semaphore = Depends(get_engine_slot),
):
    raw = await audio.read()
    # decode_audio and transcribe are both synchronous and CPU/GPU-bound. Run
    # them in a worker thread: in an `async def` they block the whole event loop,
    # which makes /health unanswerable for the duration of every transcription.
    try:
        samples, duration = await asyncio.to_thread(decode_audio, raw)
    except ValueError as exc:
        return JSONResponse(
            {"message": str(exc), "code": "audio_undecodable"}, status_code=400
        )

    # Checked here, after decoding, because this is the only place the true
    # duration is known -- and before the slot, so an over-long clip never
    # occupies the engine (spec §4.1 / §5 `audio_too_long`).
    if duration > settings.max_audio_seconds:
        return JSONResponse(
            {
                "message": (
                    f"Recording is {round(duration)}s; the limit is "
                    f"{round(settings.max_audio_seconds)}s"
                ),
                "code": "audio_too_long",
            },
            status_code=413,
        )

    lang = language or settings.language or None
    async with slot:
        text = await asyncio.to_thread(t.transcribe, samples, lang)
    return {
        "text": text,
        "language": lang or "auto",
        "durationMs": round(duration * 1000),
        "engine": t.name,
        "model": t.model,
    }


@app.post("/synthesize")
async def synthesize(
    payload: dict = Body(...),
    s: Synthesizer = Depends(get_synthesizer),
    slot: asyncio.Semaphore = Depends(get_engine_slot),
):
    text = (payload.get("text") or "").strip()
    voice = payload.get("voice") or DEFAULT_VOICE
    if not text:
        return JSONResponse({"message": "text is required", "code": "text_required"},
                            status_code=422)
    # Both checks run BEFORE the engine slot: a rejected request must never
    # occupy the engine the chat model is sharing a GPU with.
    if len(text) > settings.tts_max_chars:
        return JSONResponse(
            {"message": f"Text is {len(text)} characters; the limit is "
                        f"{settings.tts_max_chars}", "code": "text_too_long"},
            status_code=413,
        )
    if voice not in s.voices:
        return JSONResponse({"message": f"Unknown voice {voice!r}",
                             "code": "unknown_voice"}, status_code=422)

    async with slot:
        samples, rate = await asyncio.to_thread(s.synthesize, text, voice)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        wf.writeframes((clipped * 32767).astype("<i2").tobytes())
    return Response(content=buf.getvalue(), media_type="audio/wav")
