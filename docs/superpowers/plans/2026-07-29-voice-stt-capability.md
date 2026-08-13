# Voice ①a — STT Capability Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser Web Speech mic with a local, air-gap-safe pipeline — record in the browser, transcribe with faster-whisper in a voice sidecar, drop editable text into the composer.

**Architecture:** A new FastAPI sidecar (`backend/voice/service/`, port 8002) owns a `Transcriber` protocol with one adapter per engine, chosen by env. The backend proxies `POST /api/voice/transcribe` to it over httpx (mirroring `app/tools/builtin/execute_code.py`), enforcing auth and size limits at the edge. The frontend rewrites `useVoiceInput` onto `MediaRecorder`; the existing button and composer wiring stay.

**Tech Stack:** Python 3.10, FastAPI, faster-whisper (CTranslate2), PyAV, httpx, uv; React 19 + TanStack Query; npm.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-29-voice-stt-capability-design.md`.
- **The sidecar's deps never enter the backend's env.** `backend/voice/` has its own `pyproject.toml`; the backend only ever speaks HTTP to it.
- Engine + model are **config, not code**: `STT_ENGINE` (default `faster-whisper`), `STT_MODEL` (default `large-v3-turbo`), `STT_DEVICE`, `STT_COMPUTE_TYPE`, `STT_LANGUAGE` (default `id`, empty = auto), `STT_VAD` (default on).
- **VAD stays on by default** — it is what makes room tone return `""` instead of `". . . ."`.
- Error bodies use the existing `{message, code}` contract via `ApiError` (`app/errors.py:10`).
- No model weights in CI: every test uses `FakeTranscriber`.
- Backend commands run from `backend/` with `env -u VIRTUAL_ENV uv run`; frontend from `frontend/`.
- Existing behaviour to preserve: the transcript lands in the composer as **editable text and is never auto-sent**.

---

## File Structure

- `backend/voice/__init__.py` — **new**, package marker (Task 1).
- `backend/voice/pyproject.toml` — **new**, the sidecar's isolated deps (Task 1).
- `backend/voice/service/config.py` — **new**, env-driven settings, plain class like `sandbox/service/config.py` (Task 1).
- `backend/voice/service/engines.py` — **new**, `Transcriber` protocol + `FasterWhisperTranscriber` + `build_transcriber` (Task 1).
- `backend/voice/service/audio.py` — **new**, PyAV decode to float32 mono 16 kHz (Task 1).
- `backend/voice/service/main.py` — **new**, FastAPI app: `POST /transcribe`, `GET /health` (Task 1).
- `backend/voice/tests/` — **new**, `FakeTranscriber` route tests + decode test (Task 1).
- `backend/app/config.py` — **modify**, add `voice_service_url`, `max_audio_bytes`, `voice_timeout_seconds` (Task 2).
- `backend/app/voice/routes.py` — **new**, `POST /api/voice/transcribe`, `GET /api/voice/capabilities` (Task 2).
- `backend/app/main.py` — **modify**, include the router (Task 2).
- `backend/tests/test_voice.py` — **new**, proxy + limits + capability tests (Task 2).
- `frontend/src/hooks/useVoiceInput.ts` — **rewrite**, MediaRecorder instead of Web Speech (Task 3).
- `frontend/src/lib/api.ts` — **modify**, `transcribeAudio`, `getVoiceCapabilities` (Task 3).
- `frontend/src/components/chat/InputBar.tsx` — **modify**, consume the new hook shape (Task 3).
- `backend/scripts/setup_stt_model.py` — **new**, prefetch weights for air-gapped deploys (Task 4).
- `docs/DEPLOY.md`, `backend/.env.prod.example` — **modify** (Task 4).

---

### Task 1: The voice sidecar

Self-contained new service. Nothing else in the repo changes; it runs and is testable on its own.

**Interfaces produced:**
- `Transcriber` protocol: `transcribe(samples: np.ndarray, language: str | None) -> str`
- `build_transcriber(settings) -> Transcriber`
- `decode_audio(data: bytes) -> tuple[np.ndarray, float]` — returns (float32 mono 16 kHz, seconds)
- `POST /transcribe` (multipart `audio`, optional form `language`) → `{"text", "language", "durationMs", "engine", "model"}`
- `GET /health` → `{"status": "ok", "engine", "model", "device"}`

- [ ] **Step 1: Create the package and its isolated deps**

Create `backend/voice/__init__.py` (empty) and `backend/voice/service/__init__.py` (empty).

Create `backend/voice/pyproject.toml`:

```toml
[project]
name = "voice-service"
version = "0.1.0"
description = "STT sidecar — keeps engine deps out of the backend env"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "python-multipart>=0.0.9",
    "faster-whisper>=1.0",
    "av>=12",
    "numpy>=1.26",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]
```

- [ ] **Step 2: Settings**

Create `backend/voice/service/config.py`. Plain class, not pydantic-settings — same reasoning as `sandbox/service/config.py`: tests mutate fields directly.

```python
"""Voice service settings — engine choice is env-driven, never hardcoded."""
from __future__ import annotations

import os


class Settings:
    def __init__(self) -> None:
        self.engine: str = os.environ.get("STT_ENGINE", "faster-whisper")
        self.model: str = os.environ.get("STT_MODEL", "large-v3-turbo")
        self.device: str = os.environ.get("STT_DEVICE", "auto")
        self.compute_type: str = os.environ.get("STT_COMPUTE_TYPE", "")
        # Empty means auto-detect. Default "id": auto-detect is a documented
        # weak spot on 2-4s clips (spec §2).
        self.language: str = os.environ.get("STT_LANGUAGE", "id")
        self.vad: bool = os.environ.get("STT_VAD", "true").lower() != "false"
        self.beam_size: int = int(os.environ.get("STT_BEAM_SIZE", "5"))


settings = Settings()
```

- [ ] **Step 3: Write the failing decode test**

Create `backend/voice/tests/__init__.py` (empty) and `backend/voice/tests/test_audio.py`:

```python
"""Decode must produce float32 mono 16 kHz from whatever the browser sends."""
from __future__ import annotations

import io
import math
import wave

import numpy as np

from voice.service.audio import TARGET_SR, decode_audio


def _sine_wav(seconds: float = 1.0, sr: int = 44_100) -> bytes:
    """A 440 Hz tone as 16-bit stereo WAV at a NON-target sample rate, so the
    test exercises both resampling and downmixing."""
    frames = bytearray()
    for i in range(int(sr * seconds)):
        v = int(32767 * 0.5 * math.sin(2 * math.pi * 440 * i / sr))
        frames += int(v).to_bytes(2, "little", signed=True) * 2  # L + R
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def test_decode_resamples_and_downmixes():
    samples, duration = decode_audio(_sine_wav(1.0))

    assert samples.dtype == np.float32
    assert samples.ndim == 1                      # mono
    assert abs(len(samples) - TARGET_SR) < TARGET_SR * 0.05
    assert 0.9 < duration < 1.1
    assert 0.3 < float(np.abs(samples).max()) <= 1.0


def test_decode_rejects_garbage():
    try:
        decode_audio(b"not audio at all")
    except ValueError:
        return
    raise AssertionError("expected ValueError for undecodable input")
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend/voice && uv run pytest tests/test_audio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice.service.audio'`

- [ ] **Step 5: Implement decode**

Create `backend/voice/service/audio.py`:

```python
"""Browser audio -> model input.

MediaRecorder emits webm/opus on Chrome and mp4/aac on Safari, at whatever
sample rate the device likes. PyAV normalises both to the float32 mono 16 kHz
that faster-whisper wants -- and it is the same decode path the accuracy probe
used, so measured WER is the WER we ship.
"""
from __future__ import annotations

import io

import av
import numpy as np

TARGET_SR = 16_000


def decode_audio(data: bytes) -> tuple[np.ndarray, float]:
    """Return (float32 mono 16 kHz samples, duration_seconds).

    Raises ValueError if the bytes are not decodable audio.
    """
    try:
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise ValueError("no audio stream")
            stream = container.streams.audio[0]
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=TARGET_SR
            )
            chunks: list[np.ndarray] = []
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
    except ValueError:
        raise
    except Exception as exc:  # av raises its own error hierarchy
        raise ValueError(f"undecodable audio: {exc}") from exc

    if not chunks:
        return np.zeros(0, dtype=np.float32), 0.0
    samples = np.concatenate(chunks).astype(np.float32) / 32768.0
    return samples, len(samples) / TARGET_SR
```

- [ ] **Step 6: Run it to verify it passes**

Run: `cd backend/voice && uv run pytest tests/test_audio.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Write the failing service test**

Create `backend/voice/tests/conftest.py`:

```python
"""Fixtures — a fake engine so tests never touch model weights."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice.service import main as service_main


class FakeTranscriber:
    """Records what it was handed; returns a canned transcript."""

    name = "fake"
    model = "fake-model"
    device = "cpu"

    def __init__(self, text: str = "halo dunia") -> None:
        self.text = text
        self.calls: list[tuple[int, str | None]] = []

    def transcribe(self, samples: np.ndarray, language: str | None) -> str:
        self.calls.append((len(samples), language))
        return self.text


@pytest.fixture()
def fake() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture()
def client(fake: FakeTranscriber) -> TestClient:
    service_main.app.dependency_overrides[service_main.get_transcriber] = lambda: fake
    with TestClient(service_main.app) as c:
        yield c
    service_main.app.dependency_overrides.clear()
```

Create `backend/voice/tests/test_service.py`:

```python
"""The sidecar's wire contract."""
from __future__ import annotations

from voice.tests.test_audio import _sine_wav


def test_health_reports_what_actually_loaded(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    # Deploys need to verify the running model, not the intended one.
    assert body["model"] == "fake-model"
    assert body["engine"] == "fake"


def test_transcribe_returns_text_and_duration(client, fake):
    resp = client.post(
        "/transcribe", files={"audio": ("clip.wav", _sine_wav(1.0), "audio/wav")}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "halo dunia"
    assert 900 < body["durationMs"] < 1100
    assert body["model"] == "fake-model"


def test_transcribe_passes_language_through(client, fake):
    client.post(
        "/transcribe",
        files={"audio": ("clip.wav", _sine_wav(0.5), "audio/wav")},
        data={"language": "id"},
    )

    assert fake.calls[0][1] == "id"


def test_transcribe_defaults_language_from_settings(client, fake):
    client.post("/transcribe", files={"audio": ("c.wav", _sine_wav(0.5), "audio/wav")})

    assert fake.calls[0][1] == "id"  # config.py default


def test_undecodable_audio_is_400_not_500(client):
    resp = client.post(
        "/transcribe", files={"audio": ("x.webm", b"garbage", "audio/webm")}
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "audio_undecodable"
```

- [ ] **Step 8: Run to verify it fails**

Run: `cd backend/voice && uv run pytest tests/ -v`
Expected: FAIL — no module `voice.service.main`

- [ ] **Step 9: Implement engines**

Create `backend/voice/service/engines.py`:

```python
"""Engine adapters behind one protocol.

The whole point of the sidecar is that swapping this is cheap: add a class,
add a branch in build_transcriber, set STT_ENGINE. The Qwen adapter from
scripts/check_stt_pipeline.py drops in here unchanged when we want a rematch
on prod hardware (spec §10).
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from .config import Settings


class Transcriber(Protocol):
    name: str
    model: str
    device: str

    def transcribe(self, samples: np.ndarray, language: str | None) -> str: ...


class FasterWhisperTranscriber:
    """CTranslate2. fp16 on CUDA, int8 on CPU (no Metal backend exists)."""

    name = "faster-whisper"

    def __init__(self, s: Settings) -> None:
        from faster_whisper import WhisperModel

        device = s.device
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
        compute = s.compute_type or ("float16" if device == "cuda" else "int8")

        self.model = s.model
        self.device = device
        self.vad = s.vad
        self.beam_size = s.beam_size
        self._model = WhisperModel(s.model, device=device, compute_type=compute)

    def transcribe(self, samples: np.ndarray, language: str | None) -> str:
        segments, _ = self._model.transcribe(
            samples,
            language=language or None,
            beam_size=self.beam_size,
            vad_filter=self.vad,
        )
        return "".join(seg.text for seg in segments).strip()


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def build_transcriber(s: Settings) -> Transcriber:
    if s.engine == "faster-whisper":
        return FasterWhisperTranscriber(s)
    raise ValueError(f"unknown STT_ENGINE: {s.engine!r}")
```

- [ ] **Step 10: Implement the service**

Create `backend/voice/service/main.py`:

```python
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
    """Seam: tests override this so no weights are needed."""
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
```

- [ ] **Step 11: Run the suite to verify it passes**

Run: `cd backend/voice && uv run pytest tests/ -v`
Expected: PASS (7 tests)

- [ ] **Step 12: Commit**

```bash
git add backend/voice
git commit -m "feat(voice): STT sidecar with a swappable Transcriber seam

FastAPI service on :8002 owning faster-whisper behind a Transcriber protocol,
with its own pyproject so engine deps never touch the backend env. PyAV
decodes whatever MediaRecorder produces (webm/opus, mp4/aac) to the float32
mono 16kHz the probe measured against. Tests use a FakeTranscriber: no weights
in CI."
```

---

### Task 2: Backend proxy route + capability flag

**Consumes:** the sidecar's `POST /transcribe` and `GET /health` contracts from Task 1.
**Produces:** `POST /api/voice/transcribe` → `{text, durationMs}`; `GET /api/voice/capabilities` → `{stt: bool}`.

- [ ] **Step 1: Config**

In `backend/app/config.py`, next to `code_exec_url` (line ~92):

```python
    # --- Voice (§1a): STT sidecar. Empty disables the feature entirely, which
    #     is how the 8 GB dev Mac runs without loading any model.
    voice_service_url: str = ""
    voice_timeout_seconds: float = 120
    max_audio_bytes: int = 10 * 1024 * 1024   # 10 MiB
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_voice.py`:

```python
"""Backend voice proxy: auth, limits, and honest failure when the sidecar is down."""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.voice import routes as voice_routes


@pytest.fixture()
def sidecar_ok(monkeypatch):
    """Stub the sidecar with a transport that records the forwarded request."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body_len"] = len(request.content)
        return httpx.Response(
            200, json={"text": "halo dunia", "durationMs": 1200,
                       "language": "id", "engine": "fake", "model": "m"}
        )

    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    monkeypatch.setattr(
        voice_routes, "_transport", httpx.MockTransport(handler), raising=False
    )
    return seen


def test_transcribe_requires_auth(client):
    resp = client.post("/api/voice/transcribe",
                       files={"audio": ("c.webm", b"xx", "audio/webm")})
    assert resp.status_code == 401


def test_transcribe_proxies_and_returns_text(client, auth_headers, sidecar_ok):
    resp = client.post(
        "/api/voice/transcribe",
        files={"audio": ("c.webm", b"pretend-audio", "audio/webm")},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["text"] == "halo dunia"
    assert resp.json()["durationMs"] == 1200
    assert sidecar_ok["url"].endswith("/transcribe")


def test_oversized_audio_rejected_before_the_sidecar(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    monkeypatch.setattr(settings, "max_audio_bytes", 16)

    resp = client.post(
        "/api/voice/transcribe",
        files={"audio": ("c.webm", b"x" * 64, "audio/webm")},
        headers=auth_headers,
    )

    assert resp.status_code == 413
    assert resp.json()["code"] == "audio_too_large"


def test_sidecar_down_is_503_not_500(client, auth_headers, monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    monkeypatch.setattr(voice_routes, "_transport", httpx.MockTransport(boom),
                        raising=False)

    resp = client.post(
        "/api/voice/transcribe",
        files={"audio": ("c.webm", b"pretend", "audio/webm")},
        headers=auth_headers,
    )

    assert resp.status_code == 503
    assert resp.json()["code"] == "stt_unavailable"


def test_capabilities_false_when_unconfigured(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "")

    resp = client.get("/api/voice/capabilities", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"stt": False}


def test_capabilities_true_when_configured(client, auth_headers, sidecar_ok):
    resp = client.get("/api/voice/capabilities", headers=auth_headers)

    assert resp.json() == {"stt": True}
```

`client` and `auth_headers` are existing fixtures (`backend/tests/conftest.py:109`) used by every other suite — no new fixtures needed.

- [ ] **Step 3: Run to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_voice.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.voice'`

- [ ] **Step 4: Implement the routes**

Create `backend/app/voice/__init__.py` (empty) and `backend/app/voice/routes.py`:

```python
"""Voice routes (§1a) — a thin, authenticated proxy to the STT sidecar.

Deliberately not session-scoped: transcription has nothing to do with a
conversation, and keeping it separate lets ② and ③ call it from anywhere.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Form, UploadFile

from app.auth.deps import get_current_user
from app.config import settings
from app.errors import ApiError

router = APIRouter()

# Overridden in tests with httpx.MockTransport; None means "real network".
_transport: httpx.BaseTransport | None = None


@router.get("/capabilities")
def capabilities(_=Depends(get_current_user)) -> dict:
    """Drives whether the frontend renders the mic at all."""
    return {"stt": bool(settings.voice_service_url)}


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile,
    language: str = Form(default=""),
    _=Depends(get_current_user),
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
        async with httpx.AsyncClient(
            timeout=settings.voice_timeout_seconds, transport=_transport
        ) as hc:
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
        raise ApiError(502, "stt_failed", f"Voice service error {resp.status_code}")

    body = resp.json()
    return {"text": body.get("text", ""), "durationMs": body.get("durationMs", 0)}
```

- [ ] **Step 5: Wire the router**

In `backend/app/main.py`, beside the other `include_router` calls (~line 227):

```python
app.include_router(voice_router, prefix="/api/voice", tags=["voice"])
```

with the import alongside the existing router imports:

```python
from app.voice.routes import router as voice_router
```

- [ ] **Step 6: Run to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_voice.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the full backend suite for regressions**

Run: `env -u VIRTUAL_ENV uv run pytest -q`
Expected: the 356-passing baseline plus the 6 new tests, 0 failures.

- [ ] **Step 8: Commit**

```bash
git add backend/app/voice backend/app/config.py backend/app/main.py backend/tests/test_voice.py
git commit -m "feat(voice): authenticated /api/voice/transcribe proxy + capability flag

Enforces auth and the size cap at the edge, before any audio reaches the
sidecar, and turns transport failures into stt_unavailable/stt_timeout rather
than a 500 (same shape as the code-exec client). capabilities returns stt:false
when VOICE_SERVICE_URL is unset, so a dev box without the sidecar renders no
mic button instead of a dead one."
```

---

### Task 3: Frontend — replace the Web Speech mic

**Consumes:** `POST /api/voice/transcribe`, `GET /api/voice/capabilities` from Task 2.
**Produces:** `useVoiceInput()` returning `{isRecording, isTranscribing, isSupported, error, startRecording, stopRecording}` — note **`transcript` is gone**; the hook now resolves text through `stopRecording()`.

> **Why a rewrite and not an addition:** `useVoiceInput.ts` today is built on
> `webkitSpeechRecognition`, which streams audio to Google. It cannot work on an
> air-gapped network and must not be kept as a fallback.

- [ ] **Step 1: API client**

In `frontend/src/lib/api.ts`, beside the other upload helpers:

```typescript
export const getVoiceCapabilities = (): Promise<{ stt: boolean }> =>
  req("/api/voice/capabilities", { headers: authHeaders() }).then(r => r.json());

export async function transcribeAudio(
  blob: Blob,
  language = "",
): Promise<{ text: string; durationMs: number }> {
  const form = new FormData();
  form.append("audio", blob, "clip.webm");
  if (language) form.append("language", language);
  const res = await req("/api/voice/transcribe", {
    method: "POST",
    body: form,
    headers: authHeaders(),
  });
  return res.json();
}
```

- [ ] **Step 2: Rewrite the hook**

Replace the entire contents of `frontend/src/hooks/useVoiceInput.ts`:

```typescript
import { useCallback, useRef, useState } from "react";

import { transcribeAudio } from "@/lib/api";

/**
 * Record with MediaRecorder, transcribe on our own backend.
 *
 * Replaces the previous Web Speech API implementation, which streamed audio to
 * Google's servers and therefore could not work on the air-gapped deployment.
 * `stopRecording()` resolves with the transcript so the caller decides what to
 * do with it -- we never auto-send (WER on real speech is 5-15%).
 */
export function useVoiceInput() {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const isSupported =
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";

  const startRecording = useCallback(async () => {
    if (!isSupported || isRecording) return;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.start();
      recorderRef.current = recorder;
      setIsRecording(true);
    } catch {
      // Permission denied, or no input device.
      setError("Microphone blocked — allow access in your browser settings.");
    }
  }, [isSupported, isRecording]);

  const stopRecording = useCallback(async (): Promise<string> => {
    const recorder = recorderRef.current;
    if (!recorder) return "";

    const blob = await new Promise<Blob>((resolve) => {
      recorder.onstop = () =>
        resolve(new Blob(chunksRef.current, { type: recorder.mimeType }));
      recorder.stop();
    });
    // Release the mic indicator in the browser chrome.
    recorder.stream.getTracks().forEach((t) => t.stop());
    recorderRef.current = null;
    setIsRecording(false);

    if (blob.size === 0) return "";
    setIsTranscribing(true);
    try {
      const { text } = await transcribeAudio(blob);
      return text;
    } catch {
      setError("Transcription failed — try again.");
      return "";
    } finally {
      setIsTranscribing(false);
    }
  }, []);

  return {
    isRecording,
    isTranscribing,
    isSupported,
    error,
    startRecording,
    stopRecording,
  };
}
```

- [ ] **Step 3: Update InputBar**

In `frontend/src/components/chat/InputBar.tsx`:

Replace the destructure at line ~41:

```typescript
  const {
    isRecording,
    isTranscribing,
    isSupported: voiceSupported,
    startRecording,
    stopRecording,
  } = useVoiceInput();
  const { data: voiceCaps } = useVoiceCapabilities();
  const micEnabled = voiceSupported && !!voiceCaps?.stt;
```

**Delete** the transcript-piping effect at lines ~57-60 (`if (isRecording && transcript) setText(transcript)`) — there is no live transcript any more. Replace with a toggle handler:

```typescript
  const handleMic = async () => {
    if (isRecording) {
      const text = await stopRecording();
      // Append rather than replace: the user may have typed before recording.
      if (text) setText((prev) => (prev ? `${prev} ${text}` : text));
    } else {
      void startRecording();
    }
  };
```

Change the mic button's guard from `voiceSupported` to `micEnabled`, point `onClick` at `handleMic`, and add `disabled={isTranscribing}` so it can't be double-fired mid-upload. Keep the existing `Mic`/`MicOff` icons and tooltip; when `isTranscribing`, show `<Loader2 className="size-5 animate-spin" />` and the tooltip text "Transcribing…".

- [ ] **Step 4: Capability query**

In `frontend/src/lib/queries.ts`:

```typescript
export function useVoiceCapabilities() {
  return useQuery({
    queryKey: ["voice-capabilities"],
    queryFn: api.getVoiceCapabilities,
    staleTime: Infinity,   // a sidecar doesn't appear mid-session
  });
}
```

- [ ] **Step 5: Typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: both clean. Typecheck **must** fail first if you left any reference to the removed `transcript` field — that is the check that the rewrite is complete.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(voice): record via MediaRecorder against our own STT

Replaces the Web Speech API implementation, which streamed audio to Google and
could not work air-gapped. stopRecording() resolves with the transcript and the
caller appends it to the composer -- never auto-sent, because real-speech WER is
5-15%. The mic renders only when the browser supports capture AND the backend
reports the sidecar is configured."
```

---

### Task 4: Air-gapped model provisioning + deploy docs

**Consumes:** `STT_MODEL` from Task 1's settings.
**Produces:** `backend/scripts/setup_stt_model.py`; DEPLOY.md §3h; env example entries.

- [ ] **Step 1: Write the provisioning script**

Create `backend/scripts/setup_stt_model.py`, mirroring `setup_ocr_models.py`:

```python
#!/usr/bin/env python
"""Prefetch the faster-whisper model so prod never needs HuggingFace at runtime.

Prod is air-gapped. faster-whisper downloads CTranslate2 weights on first use,
which on that host means a hang and then a failed transcription. Run this once
per deploy -- or, with no outbound internet, run --manifest on a connected
machine, copy the listed files, and set STT_MODEL_DIR on the target.

Usage:
    uv run python scripts/setup_stt_model.py
    uv run python scripts/setup_stt_model.py --model large-v3 --manifest
"""
from __future__ import annotations

import argparse
import os
import sys

REPO_TEMPLATE = "Systran/faster-whisper-{model}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("STT_MODEL", "large-v3-turbo"))
    ap.add_argument("--dir", default=os.environ.get("STT_MODEL_DIR") or None)
    ap.add_argument("--manifest", action="store_true",
                    help="print the files to transfer instead of downloading")
    args = ap.parse_args()

    repo = REPO_TEMPLATE.format(model=args.model)
    files = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt",
             "preprocessor_config.json"]

    if args.manifest:
        print(f"# Fetch these from https://huggingface.co/{repo}/resolve/main/")
        for f in files:
            print(f"https://huggingface.co/{repo}/resolve/main/{f}")
        print("\n# Then place them in one directory and set STT_MODEL_DIR to it.")
        return

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit("huggingface_hub not installed — run this inside backend/voice's env")

    path = snapshot_download(repo_id=repo, local_dir=args.dir, allow_patterns=files)
    print(f"Model ready: {path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the manifest path works offline**

Run: `env -u VIRTUAL_ENV uv run python scripts/setup_stt_model.py --manifest`
Expected: five HuggingFace URLs for `Systran/faster-whisper-large-v3-turbo`, no network access attempted.

- [ ] **Step 3: Env example**

In `backend/.env.prod.example`, after the OCR block:

```bash
# --- Voice / STT (§1a): the sidecar runs on the GPU host, port 8002.
#     Leave VOICE_SERVICE_URL empty to disable voice entirely (no mic button).
VOICE_SERVICE_URL=http://localhost:8002
MAX_AUDIO_BYTES=10485760      # 10 MiB
# Sidecar-side knobs (set in the voice service's own environment):
#   STT_ENGINE=faster-whisper
#   STT_MODEL=large-v3-turbo   # large-v3 is ~0.7 WER points better, ~7x slower
#   STT_LANGUAGE=id            # empty = auto-detect
#   STT_VAD=true               # off => silence transcribes as ". . . ."
```

- [ ] **Step 4: DEPLOY.md**

Add `### 3h · Voice service (STT)` after the NSSM section, covering: installing
`backend/voice`'s deps with `uv sync`, running `setup_stt_model.py` once per
deploy (with the `--manifest` route for air-gapped hosts), starting
`uvicorn voice.service.main:app --port 8002`, registering it as a second NSSM
service named `rag-voice`, and verifying with
`curl http://localhost:8002/health` — noting that `/health` reports the model
that **actually loaded**, which is the only way to catch a silently wrong
`STT_MODEL`. State plainly that the service is optional: with
`VOICE_SERVICE_URL` unset the app runs exactly as before, minus the mic.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/setup_stt_model.py backend/.env.prod.example docs/DEPLOY.md
git commit -m "docs(deploy): voice sidecar provisioning and runbook

Prod is air-gapped, so faster-whisper's first-use download would hang there:
setup_stt_model.py prefetches the CT2 weights, with --manifest for hosts with
no outbound internet (same pattern as setup_ocr_models.py)."
```

---

### Task 5: Verification

- [ ] **Step 1:** `cd backend/voice && uv run pytest tests/ -v` — 7 passed.
- [ ] **Step 2:** `cd backend && env -u VIRTUAL_ENV uv run pytest -q` — 356 baseline + 6 new, 0 failures.
- [ ] **Step 3:** `cd frontend && npm run typecheck && npm run build` — both clean.
- [ ] **Step 4 (manual, needs the sidecar running):** start the voice service, reload the app, confirm the mic button appears; record clip 04's sentence ("Cari dokumen tentang retribusi parkir"); confirm the transcript lands in the composer as editable text and is **not** sent; edit it and send; confirm the chat turn behaves exactly as a typed message.
- [ ] **Step 5 (manual):** stop the voice service, reload — confirm the mic button disappears rather than erroring.
- [ ] **Step 6:** `git log --oneline main..HEAD` — spec, plan, and four task commits.

---

## Self-Review

**Spec coverage:** sidecar + Transcriber seam (§4) → Task 1; audio decode (§4.1) → Task 1 Steps 3-6; routes and error codes (§5) → Task 2; capability flag (§5) → Task 2 + Task 3 Step 4; frontend replacement (§6) → Task 3; testing strategy (§8) → Tasks 1-2 tests + Task 5; air-gapped weights and NSSM (§9) → Task 4. ✅

**Placeholder scan:** every code step carries complete code. Task 4 Step 4 describes DEPLOY.md prose rather than quoting it — acceptable, since it enumerates the exact commands and the specific claim to state. No TBD/TODO.

**Type consistency:** `Transcriber.transcribe(samples, language)`, `.name/.model/.device`, `build_transcriber`, `decode_audio -> (ndarray, float)`, `get_transcriber`, `_transport`, `voice_service_url`, `max_audio_bytes`, and the hook's `{isRecording, isTranscribing, isSupported, error, startRecording, stopRecording}` are used identically across tasks. The removal of `transcript` is called out explicitly in Task 3's Produces block, and Task 3 Step 5 uses typecheck as the proof that no consumer still references it. ✅

**Known gap, stated not hidden:** the frontend has no test framework, so Task 3 is covered only by typecheck, build, and the manual checks in Task 5. This is the third consecutive feature in that position.
