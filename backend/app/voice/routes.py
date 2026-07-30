"""Voice routes (§1a) — a thin, authenticated proxy to the STT sidecar.

Deliberately not session-scoped: transcription has nothing to do with a
conversation, and keeping it separate lets ② and ③ call it from anywhere.
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Form, UploadFile

from app.auth.deps import get_current_user
from app.config import settings
from app.errors import ApiError

router = APIRouter()


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Dependency seam — tests override this with a MockTransport client
    via `app.dependency_overrides` (same pattern as the sidecar's own
    `get_transcriber` in `backend/voice/service/main.py`)."""
    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as hc:
        yield hc


@router.get("/capabilities")
def capabilities(_=Depends(get_current_user)) -> dict:
    """Drives whether the frontend renders the mic at all."""
    return {"stt": bool(settings.voice_service_url)}


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
