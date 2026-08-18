"""Voice routes (§1a) — a thin, authenticated proxy to the STT sidecar.

Deliberately not session-scoped: transcription has nothing to do with a
conversation, and keeping it separate lets ② and ③ call it from anywhere.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Form, Request, UploadFile

from app.auth.deps import get_current_user
from app.config import settings
from app.errors import ApiError

log = logging.getLogger(__name__)

router = APIRouter()

#: Where the startup probe's verdict lives. Read by `capabilities`.
STT_READY_ATTR = "voice_stt_ready"

#: The voice styles the sidecar ships (§1b). Mirrors voice/service/tts.py --
#: the backend cannot import from the sidecar's separate uv project.
VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Dependency seam — tests override this with a MockTransport client
    via `app.dependency_overrides` (same pattern as the sidecar's own
    `get_transcriber` in `backend/voice/service/main.py`)."""
    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as hc:
        yield hc


def _probe_client() -> httpx.AsyncClient:
    """Test seam for `probe_sidecar`.

    The probe runs from the lifespan, before any request exists, so there is no
    `Depends` to override the way `/transcribe` does — tests patch this instead.
    """
    return httpx.AsyncClient(timeout=settings.voice_probe_timeout_seconds)


async def probe_sidecar() -> bool:
    """Did the sidecar's /health answer? Called once, from the backend's lifespan.

    Spec §5: `stt` is true when VOICE_SERVICE_URL is set **and** /health
    answered. `bool(url)` alone is not the same claim -- a URL in .env with no
    process behind it produced a mic button for every user and a 503 on every
    press, which is precisely the dead-control-that-looks-available this feature
    exists to remove.

    Never raises. A voice probe must not be able to stop the backend from
    starting; the failure mode is "no mic button", not "no app".
    """
    if not settings.voice_service_url:
        return False
    url = f"{settings.voice_service_url}/health"
    try:
        async with _probe_client() as hc:
            resp = await hc.get(url)
    except Exception as exc:
        log.warning("Voice sidecar probe failed (%s) — mic disabled: %s", url, exc)
        return False
    if resp.status_code != 200:
        log.warning(
            "Voice sidecar %s answered %s — mic disabled", url, resp.status_code
        )
        return False
    log.info("Voice sidecar ready at %s", settings.voice_service_url)
    return True


@router.get("/capabilities")
def capabilities(request: Request, _=Depends(get_current_user)) -> dict:
    """Drives whether the frontend renders the mic at all.

    Reports the startup probe's verdict rather than re-probing per request: this
    is called on every chat page load, and a per-request round-trip to the
    sidecar would put its latency in front of the composer rendering.
    """
    return {"stt": bool(getattr(request.app.state, STT_READY_ATTR, False))}


#: Codes the sidecar is allowed to speak in its own voice. A 4xx from the sidecar
#: is a statement about *this recording* -- the user can act on it -- so
#: flattening it to `stt_failed` (502) throws away the only useful part. 5xx is
#: different: that is the sidecar's problem, not the user's, and stays
#: `stt_failed`. The allow-list keeps the client's `code` vocabulary a closed set
#: rather than whatever a future sidecar version happens to emit.
_SIDECAR_CLIENT_CODES = {"audio_too_long", "audio_undecodable"}


def _sidecar_error(resp: httpx.Response) -> ApiError:
    """Translate a sidecar error response into our `{message, code}` contract."""
    if 400 <= resp.status_code < 500:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        if code in _SIDECAR_CLIENT_CODES:
            message = (body.get("message") if isinstance(body, dict) else None) or code
            return ApiError(resp.status_code, code, str(message))
    return ApiError(502, "stt_failed", f"Voice service error {resp.status_code}")


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile,
    language: str = Form(default=""),
    _=Depends(get_current_user),
    hc: httpx.AsyncClient = Depends(get_http_client),
) -> dict:
    if not settings.voice_service_url:
        raise ApiError(503, "stt_unavailable", "Speech-to-text is not configured")

    raw = await audio.read()
    if len(raw) > settings.max_audio_bytes:
        raise ApiError(
            413, "audio_too_large",
            f"Recording exceeds {settings.max_audio_bytes // (1024 * 1024)} MB",
        )

    url = f"{settings.voice_service_url}/transcribe"
    try:
        resp = await hc.post(
            url,
            files={"audio": (audio.filename or "clip", raw,
                             audio.content_type or "application/octet-stream")},
            data={"language": language},
        )
    except httpx.TimeoutException as exc:
        raise ApiError(504, "stt_timeout", "Transcription timed out") from exc
    except httpx.RequestError as exc:
        raise ApiError(503, "stt_unavailable", f"Voice service unavailable: {exc}") from exc

    if resp.status_code >= 400:
        raise _sidecar_error(resp)

    body = resp.json()
    return {"text": body.get("text", ""), "durationMs": body.get("durationMs", 0)}
