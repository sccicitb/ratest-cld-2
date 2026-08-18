# Voice §1b — TTS Capability Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser's Web Speech API with a local Supertonic 3 engine, so answers are read aloud in a consistent Indonesian voice that works air-gapped, with a per-user voice preference defaulting to F2.

**Architecture:** The existing `backend/voice/` sidecar gains a `Synthesizer` protocol beside its `Transcriber`, and a `POST /synthesize` endpoint. The backend proxies it at `POST /api/voice/speak`, resolving the voice from the authenticated user's new `users.voice` column. The frontend rewrites `useVoiceSynthesis` to call that endpoint, keeping its exported shape so `MessageBubble` is untouched, and adds a voice picker to the profile dropdown.

**Tech Stack:** Python 3.10, FastAPI, `supertonic` 1.3.1 (ONNX Runtime), SQLAlchemy + Alembic, React 19, TanStack Query, Zustand.

**Spec:** `docs/superpowers/specs/2026-08-18-voice-tts-capability-design.md`

## Global Constraints

- Backend commands run from `backend/` as `env -u VIRTUAL_ENV uv run …`; sidecar commands from `backend/voice/`. A bare `pytest` picks up the wrong interpreter in this repo.
- **Baselines before this plan:** backend suite **388 passed**; sidecar suite **19 passed**. Every task states its new expected totals.
- The ten valid voice styles are exactly: `M1 M2 M3 M4 M5 F1 F2 F3 F4 F5`. The default is `F2`.
- A voice name from a client is **untrusted input** that resolves to a filesystem path inside Supertonic. It is whitelisted in the backend on write AND independently in the sidecar. Never pass it through unvalidated.
- Text normalization is **engine-specific** and lives inside the Supertonic adapter, never in a shared layer — the same normalization measurably *hurt* Piper.
- Indonesian strings in code and tests are not translated.
- Voice is OFF unless the sidecar reports it. No feature may make the read button appear when `capabilities.tts` is false.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/voice/service/tts.py` (create) | `Synthesizer` protocol, `SupertonicSynthesizer`, Indonesian text normalization, `build_synthesizer`. |
| `backend/voice/service/config.py` (modify) | TTS knobs. |
| `backend/voice/pyproject.toml` (modify) | `supertonic` dependency. |
| `backend/voice/service/main.py` (modify) | Lifespan builds the synthesizer; `POST /synthesize`; `/health` reports `tts`. |
| `backend/voice/tests/test_tts.py` (create) | Normalization + synthesis behaviour. |
| `backend/app/models/__init__.py` (modify) | `User.voice` column. |
| `backend/migrations/versions/*_add_user_voice.py` (create) | The column migration. |
| `backend/app/schemas/__init__.py` (modify) | `UserOut.voice`, `UpdateMeRequest`. |
| `backend/app/auth/routes.py` (modify) | `PATCH /me`. |
| `backend/app/voice/routes.py` (modify) | `POST /speak`, `capabilities.tts`, TTS probe. |
| `backend/app/config.py` (modify) | `max_tts_chars`. |
| `backend/tests/test_voice.py`, `backend/tests/test_auth.py` (modify) | Proxy + preference tests. |
| `frontend/src/lib/api.ts`, `src/types/api.ts` (modify) | `speakText`, `updateMe`, `User.voice`, capability type. |
| `frontend/src/hooks/useVoiceSynthesis.ts` (rewrite) | Call our API instead of `speechSynthesis`. |
| `frontend/src/components/chat/MessageBubble.tsx` (modify) | Gate the read button on `capabilities.tts`. |
| `frontend/src/components/layout/VoiceDialog.tsx` (create) | The ten-voice picker. |
| `frontend/src/components/layout/ProfileFooter.tsx` (modify) | "Voice…" menu item. |
| `backend/scripts/setup_tts_models.py` (create) | Air-gapped provisioning. |
| `backend/.env.prod.example`, `docs/DEPLOY.md` (modify) | Runbook. |

---

### Task 1: Sidecar — the synthesizer seam and its text front-end

**Files:**
- Create: `backend/voice/service/tts.py`
- Modify: `backend/voice/service/config.py`, `backend/voice/pyproject.toml`
- Test: `backend/voice/tests/test_tts.py` (create)

**Interfaces:**
- Produces: `VOICES: list[str]`, `DEFAULT_VOICE: str`, `tts_normalize(text: str) -> str`, `class Synthesizer(Protocol)` with attributes `name: str`, `model: str`, `voices: list[str]` and method `synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]` returning `(samples, sample_rate)`, and `build_synthesizer(s: Settings) -> Synthesizer`. Task 2 calls `build_synthesizer` and `Synthesizer.synthesize`.

Background: this sidecar is a **standalone uv project**. It cannot import from `backend/scripts/`, so the Indonesian number speller proven in `check_tts_pipeline.py` is reproduced here. That duplication is deliberate — the probe is a dev tool, this is production — and the plan gives you the full code rather than asking you to copy it.

- [ ] **Step 1: Add the dependency**

In `backend/voice/pyproject.toml`, add to `dependencies` after the `onnxruntime` pin:

```toml
    # TTS (§1b). Requires onnxruntime>=1.19.0, satisfied by the pin above --
    # verified by resolving both in one environment before choosing to extend
    # this sidecar rather than stand up a second one.
    "supertonic>=1.3.1",
```

Then run `cd backend/voice && env -u VIRTUAL_ENV uv sync` and confirm it resolves without changing the `onnxruntime` version.

- [ ] **Step 2: Write the failing tests**

Create `backend/voice/tests/test_tts.py`:

```python
"""TTS text front-end (§1b).

The measured reason this exists: raw Supertonic scored 21.1% WER on the
numbers category, reading "Rp 875.000.000" as "2775 ribu" and "RT 03 RW 07"
as "RT NOA 3 RW Loara Juju". Expanding digits first dropped that to 4.5%.
"""
from __future__ import annotations

import pytest

from voice.service.tts import VOICES, DEFAULT_VOICE, tts_normalize


def test_default_voice_is_f2_and_is_a_real_voice():
    assert DEFAULT_VOICE == "F2"
    assert DEFAULT_VOICE in VOICES
    assert len(VOICES) == 10


def test_currency_keeps_the_scale_word_with_the_amount():
    """"Rp 2,3 miliar" is "dua koma tiga MILIAR rupiah".

    A naive substitution straddles the scale word and says "dua koma tiga
    rupiah miliar", which is a different (and meaningless) quantity.
    """
    assert tts_normalize("Rp 2,3 miliar") == "dua koma tiga miliar rupiah"


def test_large_currency_amount_is_spelled_in_full():
    assert tts_normalize("Rp 875.000.000") == "delapan ratus tujuh puluh lima juta rupiah"


def test_leading_zero_is_spoken_digit_by_digit():
    """"RT 03" is "RT nol tiga". Dropping the zero is what produced the
    "RW Loara Juju" garbage in the probe."""
    assert tts_normalize("RT 03 RW 07") == "RT nol tiga RW nol tujuh"


def test_punctuation_and_casing_survive():
    """Commas and full stops are where the engine takes its breath; casing
    carries acronyms. The STT-side normalizer flattens both, which is right
    for scoring and wrong for speaking."""
    out = tts_normalize("Data BPS per 31 Desember 2025, mencatat 14 kelurahan.")
    assert out.startswith("Data BPS per tiga puluh satu Desember")
    assert out.endswith("empat belas kelurahan.")
    assert "," in out


def test_text_without_numbers_is_returned_unchanged():
    assert tts_normalize("Dokumen ditemukan.") == "Dokumen ditemukan."
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd backend/voice && env -u VIRTUAL_ENV uv run pytest tests/test_tts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice.service.tts'`

- [ ] **Step 4: Create the module**

Create `backend/voice/service/tts.py`:

```python
"""TTS engines behind one protocol (§1b) — the mirror of `engines.py`.

Supertonic 3: 99M params, 31 languages including `id`, ONNX Runtime, ~385 MB
on disk, RTF ~0.195 on CPU. Chosen over Piper on measured WER over Indonesian
text (2.5% vs 3.7% mean) plus a 3x lead on English loanwords, and because §3
real-time voice needs expressive range that a one-voice-per-file engine
cannot give.

Text normalization lives HERE, inside the adapter, not in a shared layer:
the identical normalization improved Supertonic (numbers 21.1% -> 4.5%) and
made Piper WORSE (4.5% -> 8.9%), because Piper ships its own front-end. A
future Piper adapter simply would not call it.
"""
from __future__ import annotations

import re
from typing import Protocol

import numpy as np

from .config import Settings

#: The voice styles Supertonic 3 ships. A voice name arriving from a client is
#: untrusted input that resolves to `voice_styles/<name>.json`, so this list is
#: a whitelist, not documentation.
VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]

#: One of five voices that scored 0.0% WER on the round-trip sweep.
DEFAULT_VOICE = "F2"

_UNITS = ["nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh",
          "delapan", "sembilan"]

_NUMBER_RE = re.compile(r"\d[\d.,]*")
# The scale word must be swallowed with the amount -- see the docstring on
# tts_normalize.
_RUPIAH_RE = re.compile(
    r"\bRp\.?\s*(\d[\d.,]*)(\s+(?:triliun|miliar|juta|ribu))?", re.IGNORECASE
)


def spell_id(n: int) -> str:
    """Indonesian cardinal for *n* (handles the se- prefixes)."""
    if n < 10:
        return _UNITS[n]
    if n < 12:
        return {10: "sepuluh", 11: "sebelas"}[n]
    if n < 20:
        return f"{_UNITS[n - 10]} belas"
    if n < 100:
        head, rest = divmod(n, 10)
        return f"{_UNITS[head]} puluh" + (f" {spell_id(rest)}" if rest else "")
    for div, word in ((10**12, "triliun"), (10**9, "miliar"),
                      (10**6, "juta"), (1000, "ribu"), (100, "ratus")):
        if n >= div:
            head, rest = divmod(n, div)
            prefix = ("seratus" if div == 100 and head == 1
                      else "seribu" if div == 1000 and head == 1
                      else f"{spell_id(head)} {word}")
            return prefix + (f" {spell_id(rest)}" if rest else "")
    return str(n)


def _spell_token(tok: str) -> str:
    """Spell one numeric token, preserving trailing sentence punctuation."""
    trail = ""
    while tok and tok[-1] in ".,":
        trail = tok[-1] + trail
        tok = tok[:-1]
    if not tok:
        return trail
    if tok.isdigit() and tok.startswith("0"):
        # "RT 03" is "RT nol tiga": the zero is part of how it is said aloud,
        # and dropping it made the engine invent words.
        return " ".join(_UNITS[int(d)] for d in tok) + trail
    tok = tok.replace(".", "")  # thousands separator
    if "," in tok:
        whole, _, frac = tok.partition(",")
        if whole.isdigit() and frac.isdigit():
            digits = " ".join(_UNITS[int(d)] for d in frac)
            return f"{spell_id(int(whole))} koma {digits}" + trail
        return (spell_id(int(whole)) if whole.isdigit() else tok) + trail
    return (spell_id(int(tok)) if tok.isdigit() else tok) + trail


def tts_normalize(text: str) -> str:
    """Expand digits and `Rp` for synthesis, PRESERVING punctuation and case.

    Written Indonesian puts the currency marker before the digits and the
    scale after them, so "Rp 2,3 miliar" must become "dua koma tiga MILIAR
    rupiah". A substitution that ignores the scale word emits "dua koma tiga
    rupiah miliar".
    """
    text = _RUPIAH_RE.sub(
        lambda m: f"{_spell_token(m.group(1))}{m.group(2) or ''} rupiah", text
    )
    return _NUMBER_RE.sub(lambda m: _spell_token(m.group(0)), text)


class Synthesizer(Protocol):
    name: str
    model: str
    voices: list[str]

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]: ...


class SupertonicSynthesizer:
    """Supertonic 3 via ONNX Runtime. Whole-text and blocking: the library
    has no incremental output (`max_chunk_length` is internal text
    segmentation). Fine for §1b, which reads a finished answer; §3 will need
    sentence chunking written by us."""

    name = "supertonic"

    def __init__(self, s: Settings) -> None:
        from supertonic import TTS

        self.model = s.tts_model_dir or "supertonic-3"
        self.voices = list(VOICES)
        self._steps = s.tts_steps
        self._speed = s.tts_speed
        self._tts = TTS(s.tts_model_dir) if s.tts_model_dir else TTS(auto_download=True)
        # Styles are resolved once at load: doing it per request would put a
        # file read in front of every synthesis, and it is also the second
        # place an unknown voice would reach the filesystem.
        self._styles = {v: self._tts.get_voice_style(voice_name=v) for v in VOICES}

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]:
        if voice not in self._styles:
            raise ValueError(f"unknown voice: {voice!r}")
        wav, _duration = self._tts.synthesize(
            text=tts_normalize(text),
            lang="id",
            voice_style=self._styles[voice],
            total_steps=self._steps,
            speed=self._speed,
        )
        return wav, 44_100


def build_synthesizer(s: Settings) -> Synthesizer:
    if s.tts_engine == "supertonic":
        return SupertonicSynthesizer(s)
    raise ValueError(f"unknown TTS_ENGINE: {s.tts_engine!r}")
```

- [ ] **Step 5: Add the settings**

In `backend/voice/service/config.py`, inside `Settings.__init__`, after the existing `max_audio_seconds` block:

```python
        # --- TTS (§1b) ---
        self.tts_engine: str = os.environ.get("TTS_ENGINE", "supertonic")
        self.tts_model_dir: str = os.environ.get("TTS_MODEL_DIR", "")
        self.tts_steps: int = int(os.environ.get("TTS_STEPS", "8"))
        self.tts_speed: float = float(os.environ.get("TTS_SPEED", "1.0"))
        # A very long answer is a multi-second GPU hold at RTF ~0.2. The
        # sidecar is the only component that knows the real cost.
        self.tts_max_chars: int = int(os.environ.get("TTS_MAX_CHARS", "5000"))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend/voice && env -u VIRTUAL_ENV uv run pytest tests/test_tts.py -q`
Expected: PASS — 6 passed. These tests import only `tts_normalize`, `VOICES`, and `DEFAULT_VOICE`, so no model is loaded and nothing is downloaded.

- [ ] **Step 7: Run the whole sidecar suite**

Run: `cd backend/voice && env -u VIRTUAL_ENV uv run pytest tests/ -q`
Expected: **25 passed** (19 baseline + 6 new).

- [ ] **Step 8: Commit**

```bash
git add backend/voice/service/tts.py backend/voice/service/config.py \
        backend/voice/pyproject.toml backend/voice/uv.lock backend/voice/tests/test_tts.py
git commit -m "feat(voice): Supertonic synthesizer seam and its Indonesian text front-end"
```

---

### Task 2: Sidecar — `/synthesize` and the `tts` health field

**Files:**
- Modify: `backend/voice/service/main.py`
- Test: `backend/voice/tests/test_tts_service.py` (create), `backend/voice/tests/conftest.py`

**Interfaces:**
- Consumes: `build_synthesizer`, `Synthesizer`, `VOICES`, `DEFAULT_VOICE`, `tts_normalize` from Task 1.
- Produces: `POST /synthesize` accepting JSON `{"text": str, "voice": str}` and returning `audio/wav` bytes; `GET /health` gaining a `"tts"` object `{"engine": str, "model": str, "voices": [str]}` or `null`. Task 4 consumes both.

Background: `main.py` already holds `_transcriber` and `_engine_slot` module globals, built in `lifespan`, with `get_transcriber()` / `get_engine_slot()` as `Depends` seams. Follow that exact shape — do not invent a different one. The existing `client` fixture patches `build_transcriber` **before** the app's lifespan runs; you must patch `build_synthesizer` the same way or a real 385 MB model will download during tests.

- [ ] **Step 1: Extend the test fixture**

In `backend/voice/tests/conftest.py`, add a fake synthesizer above the `client` fixture:

```python
class FakeSynthesizer:
    """Returns a fixed tone; records what it was asked to say."""

    name = "fake-tts"
    model = "fake-tts-model"

    def __init__(self) -> None:
        from voice.service.tts import VOICES

        self.voices = list(VOICES)
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, voice: str):
        if voice not in self.voices:
            raise ValueError(f"unknown voice: {voice!r}")
        self.calls.append((text, voice))
        return np.zeros(4410, dtype=np.float32), 44_100


@pytest.fixture()
def fake_tts() -> "FakeSynthesizer":
    return FakeSynthesizer()
```

Then change the `client` fixture signature to `def client(fake: FakeTranscriber, fake_tts: FakeSynthesizer, monkeypatch: pytest.MonkeyPatch) -> TestClient:` and add, next to the existing `build_transcriber` patch:

```python
    # Same reasoning as build_transcriber: lifespan calls build_synthesizer
    # directly, so patching the dependency alone would still download and
    # load a real 385 MB model at TestClient startup.
    monkeypatch.setattr(service_main, "build_synthesizer", lambda settings: fake_tts)
    service_main.app.dependency_overrides[service_main.get_synthesizer] = lambda: fake_tts
```

- [ ] **Step 2: Write the failing tests**

Create `backend/voice/tests/test_tts_service.py`:

```python
"""The /synthesize endpoint (§1b)."""
from __future__ import annotations

import wave
import io


def test_health_reports_tts_alongside_stt(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["tts"]["engine"] == "fake-tts"
    assert "F2" in body["tts"]["voices"]


def test_synthesize_returns_wav_bytes(client, fake_tts):
    resp = client.post("/synthesize", json={"text": "Dokumen ditemukan.", "voice": "F2"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(resp.content), "rb") as wf:
        assert wf.getframerate() == 44_100
        assert wf.getnframes() > 0
    assert fake_tts.calls == [("Dokumen ditemukan.", "F2")]


def test_synthesize_normalizes_numbers_before_speaking(client, fake_tts):
    """The engine must receive spelled-out digits, not "Rp 2,3 miliar"."""
    client.post("/synthesize", json={"text": "Rp 2,3 miliar", "voice": "F2"})
    spoken, _voice = fake_tts.calls[0]
    assert spoken == "dua koma tiga miliar rupiah"


def test_synthesize_rejects_an_unknown_voice(client, fake_tts):
    """A voice name resolves to a filesystem path inside the engine, so an
    unknown one must be refused here rather than handed on."""
    resp = client.post("/synthesize", json={"text": "halo", "voice": "../../etc/passwd"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "unknown_voice"
    assert fake_tts.calls == []


def test_synthesize_rejects_text_over_the_cap(client, fake_tts):
    from voice.service.config import settings

    resp = client.post(
        "/synthesize", json={"text": "a" * (settings.tts_max_chars + 1), "voice": "F2"}
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "text_too_long"
    assert fake_tts.calls == []


def test_synthesize_defaults_the_voice_when_omitted(client, fake_tts):
    resp = client.post("/synthesize", json={"text": "halo"})
    assert resp.status_code == 200
    assert fake_tts.calls[0][1] == "F2"
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend/voice && env -u VIRTUAL_ENV uv run pytest tests/test_tts_service.py -q`
Expected: FAIL — the fixture references `service_main.build_synthesizer`, which does not exist yet.

- [ ] **Step 4: Implement in `main.py`**

Add to the imports:

```python
import io
import wave
from fastapi import Body
from fastapi.responses import Response
from .tts import DEFAULT_VOICE, Synthesizer, build_synthesizer
```

Add a module global beside `_transcriber`:

```python
_synthesizer: Synthesizer | None = None
```

In `lifespan`, extend the `global` line to `global _transcriber, _engine_slot, _synthesizer` and after the transcriber is built:

```python
    _synthesizer = build_synthesizer(settings)
    log.info("voice: loaded TTS %s / %s", _synthesizer.name, _synthesizer.model)
```

and in the teardown after `yield`, add `_synthesizer = None`.

Add the dependency seam next to `get_transcriber`:

```python
def get_synthesizer() -> Synthesizer:
    """Mirror of get_transcriber -- see its docstring for why the real test
    seam is patching build_synthesizer, not overriding this."""
    assert _synthesizer is not None, "synthesizer not initialised"
    return _synthesizer
```

Replace the `health` function body's return with:

```python
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
```

Add the endpoint after `transcribe`:

```python
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
```

Add `import numpy as np` to the imports if it is not already there.

- [ ] **Step 5: Run to verify they pass**

Run: `cd backend/voice && env -u VIRTUAL_ENV uv run pytest tests/test_tts_service.py -q`
Expected: PASS — 6 passed.

- [ ] **Step 6: Run the whole sidecar suite**

Run: `cd backend/voice && env -u VIRTUAL_ENV uv run pytest tests/ -q`
Expected: **31 passed** (25 after Task 1 + 6 new). Watch `tests/test_service.py` and `tests/test_concurrency.py` — they assert on `/health` and on the shared engine slot, and are the likeliest place this change breaks something.

- [ ] **Step 7: Commit**

```bash
git add backend/voice/service/main.py backend/voice/tests/
git commit -m "feat(voice): sidecar /synthesize endpoint and tts health reporting"
```

---

### Task 3: Backend — the per-user voice preference

**Files:**
- Modify: `backend/app/models/__init__.py` (the `User` class, around line 75), `backend/app/schemas/__init__.py` (`UserOut`, around line 38), `backend/app/auth/routes.py`
- Create: `backend/migrations/versions/<rev>_add_user_voice.py`
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Produces: `User.voice: str` (default `"F2"`), `UserOut.voice: str`, `VOICES: list[str]` re-exported from `app.voice.routes`, and `PATCH /api/auth/me` accepting `{"voice": str}` returning `UserOut`. Task 4 reads `user.voice`; Task 5 reads `UserOut.voice`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_auth.py`:

```python
# --- Voice preference (§1b) --------------------------------------------------


def test_new_user_defaults_to_f2(client, auth_headers):
    body = client.get("/api/auth/me", headers=auth_headers).json()
    assert body["voice"] == "F2"


def test_patch_me_updates_the_voice(client, auth_headers):
    resp = client.patch("/api/auth/me", json={"voice": "M2"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["voice"] == "M2"
    assert client.get("/api/auth/me", headers=auth_headers).json()["voice"] == "M2"


def test_patch_me_rejects_an_unknown_voice(client, auth_headers):
    """The value reaches a filesystem path inside the TTS engine, so an
    invalid one must never be persisted."""
    resp = client.patch(
        "/api/auth/me", json={"voice": "../../etc/passwd"}, headers=auth_headers
    )
    assert resp.status_code == 422
    assert client.get("/api/auth/me", headers=auth_headers).json()["voice"] == "F2"


def test_patch_me_requires_authentication(client):
    assert client.patch("/api/auth/me", json={"voice": "M2"}).status_code == 401
```

If `auth_headers` is not an existing fixture in this file, use whatever fixture the neighbouring tests use to authenticate — read the top of `backend/tests/test_auth.py` and follow it exactly rather than inventing one.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_auth.py -q -k voice`
Expected: FAIL — `KeyError: 'voice'` / 405 on PATCH.

- [ ] **Step 3: Add the column**

In `backend/app/models/__init__.py`, in `class User`, after the `disabled` column:

```python
    # §1b: TTS voice style. Server default so existing rows need no backfill.
    voice: Mapped[str] = mapped_column(String, default="F2", server_default="F2")
```

- [ ] **Step 4: Generate and check the migration**

Run:

```bash
cd backend && env -u VIRTUAL_ENV uv run alembic revision --autogenerate -m "add user voice"
```

Open the generated file. It must use `op.batch_alter_table` (SQLite cannot ALTER in place — every existing migration in this repo does this) and read:

```python
def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('voice', sa.String(), server_default='F2', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('voice')
```

Fix it by hand if autogenerate produced anything else. Then run `env -u VIRTUAL_ENV uv run alembic upgrade head` and confirm it applies cleanly.

- [ ] **Step 5: Expose it and add the route**

In `backend/app/schemas/__init__.py`, add to `UserOut` after `disabled`:

```python
    voice: str = "F2"
```

and add a request schema near the other auth schemas:

```python
class UpdateMeRequest(CamelModel):
    voice: str
```

In `backend/app/auth/routes.py`, import `VOICES` and `ApiError`, then add after the existing `me` route:

```python
@router.patch("/me", response_model=UserOut)
def update_me(body: UpdateMeRequest, user: CurrentUser, db: DbSession) -> UserOut:
    """Update the caller's own preferences.

    `voice` is whitelisted rather than stored as given: it resolves to
    `voice_styles/<name>.json` inside the TTS engine, so an arbitrary string
    from a client is a path, not a label. The sidecar validates again on its
    own -- it is separately reachable on its own port.
    """
    if body.voice not in VOICES:
        raise ApiError(422, "unknown_voice", f"Unknown voice {body.voice!r}")
    user.voice = body.voice
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user, from_attributes=True)
```

Add `VOICES` to `backend/app/voice/routes.py` so there is exactly one list in the backend:

```python
#: The voice styles the sidecar ships (§1b). Mirrors voice/service/tts.py --
#: the backend cannot import from the sidecar's separate uv project.
VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]
```

- [ ] **Step 6: Run to verify they pass**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_auth.py -q`
Expected: PASS — all auth tests including the 4 new ones.

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest -q`
Expected: **392 passed** (388 baseline + 4 new).

- [ ] **Step 8: Commit**

```bash
git add backend/app/models backend/app/schemas backend/app/auth/routes.py \
        backend/app/voice/routes.py backend/migrations/versions backend/tests/test_auth.py
git commit -m "feat(voice): per-user TTS voice preference, defaulting to F2"
```

---

### Task 4: Backend — the `/speak` proxy and the `tts` capability

**Files:**
- Modify: `backend/app/voice/routes.py`, `backend/app/config.py`
- Test: `backend/tests/test_voice.py`

**Interfaces:**
- Consumes: `User.voice` (Task 3), the sidecar's `POST /synthesize` and `/health.tts` (Task 2).
- Produces: `POST /api/voice/speak` accepting `{"text": str}` and returning `audio/wav`; `GET /api/voice/capabilities` returning `{"stt": bool, "tts": bool}`. Task 5 calls both.

Background: `app/voice/routes.py` already has `STT_READY_ATTR`, `probe_sidecar()`, `get_http_client()` (the test seam, overridden with `httpx.MockTransport`), `_sidecar_error()`, and `_SIDECAR_CLIENT_CODES`. Reuse all of them; do not build a parallel set.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_voice.py`. Read the top of that file first and reuse its existing mock-transport helper rather than writing a new one; the tests below assume a helper that installs an `httpx.MockTransport` handler via `app.dependency_overrides[get_http_client]`.

```python
# --- TTS proxy (§1b) ---------------------------------------------------------


def test_speak_requires_authentication(client):
    assert client.post("/api/voice/speak", json={"text": "halo"}).status_code == 401


def test_speak_forwards_the_users_stored_voice(client, auth_headers, monkeypatch):
    """The voice comes from the authenticated user, never from the request
    body -- the same rule as ToolContext scope in §7."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=b"RIFFfake", headers={"content-type": "audio/wav"})

    _mock_sidecar(client, handler)
    client.patch("/api/auth/me", json={"voice": "M3"}, headers=auth_headers)

    resp = client.post(
        "/api/voice/speak",
        json={"text": "Dokumen ditemukan.", "voice": "M5"},  # ignored on purpose
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.content == b"RIFFfake"
    assert seen["voice"] == "M3"
    assert seen["text"] == "Dokumen ditemukan."


def test_speak_returns_503_when_voice_is_not_configured(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "")
    resp = client.post("/api/voice/speak", json={"text": "halo"}, headers=auth_headers)
    assert resp.status_code == 503
    assert resp.json()["code"] == "tts_unavailable"


def test_speak_rejects_text_over_the_backend_cap(client, auth_headers):
    resp = client.post(
        "/api/voice/speak",
        json={"text": "a" * (settings.max_tts_chars + 1)},
        headers=auth_headers,
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "text_too_long"


def test_capabilities_reports_tts_from_the_probe(client, auth_headers):
    client.app.state.voice_tts_ready = True
    body = client.get("/api/voice/capabilities", headers=auth_headers).json()
    assert body == {"stt": body["stt"], "tts": True}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_voice.py -q -k "speak or tts"`
Expected: FAIL — 404 on `/api/voice/speak`, `KeyError: 'tts'`.

- [ ] **Step 3: Add the setting**

In `backend/app/config.py`, in the Voice block after `max_audio_bytes`:

```python
    # §1b: cap on text sent for synthesis. The sidecar enforces its own limit
    # too; this one stops a large body before it crosses the wire.
    max_tts_chars: int = 5000
```

- [ ] **Step 4: Implement the route and the probe**

In `backend/app/voice/routes.py`, add beside `STT_READY_ATTR`:

```python
#: Where the startup probe's TTS verdict lives.
TTS_READY_ATTR = "voice_tts_ready"
```

Change `probe_sidecar` to return both verdicts. Its current signature is
`async def probe_sidecar() -> bool` returning `False` on every failure path;
change it to `-> tuple[bool, bool]` returning `(stt_ready, tts_ready)`,
returning `(False, False)` on every existing failure path, and on success:

```python
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    tts_ready = bool(isinstance(body, dict) and body.get("tts"))
    log.info("Voice sidecar ready at %s (tts=%s)", settings.voice_service_url, tts_ready)
    return True, tts_ready
```

Update its caller in `backend/app/main.py`'s lifespan to unpack the tuple and set both attributes on `app.state`. Find the existing line that assigns `STT_READY_ATTR` and mirror it for `TTS_READY_ATTR`.

Change `capabilities` to:

```python
    return {
        "stt": bool(getattr(request.app.state, STT_READY_ATTR, False)),
        "tts": bool(getattr(request.app.state, TTS_READY_ATTR, False)),
    }
```

Add the route after `transcribe`:

```python
@router.post("/speak")
async def speak(
    body: dict = Body(...),
    user=Depends(get_current_user),
    hc: httpx.AsyncClient = Depends(get_http_client),
):
    if not settings.voice_service_url:
        raise ApiError(503, "tts_unavailable", "Text-to-speech is not configured")

    text = (body.get("text") or "").strip()
    if not text:
        raise ApiError(422, "text_required", "text is required")
    if len(text) > settings.max_tts_chars:
        raise ApiError(413, "text_too_long",
                       f"Text exceeds {settings.max_tts_chars} characters")

    # The voice is the caller's stored preference. A `voice` in the request
    # body is ignored: this is server-controlled scope, like ToolContext.
    try:
        resp = await hc.post(
            f"{settings.voice_service_url}/synthesize",
            json={"text": text, "voice": user.voice},
        )
    except httpx.TimeoutException as exc:
        raise ApiError(504, "tts_timeout", "Synthesis timed out") from exc
    except httpx.RequestError as exc:
        raise ApiError(503, "tts_unavailable", f"Voice service unavailable: {exc}") from exc

    if resp.status_code >= 400:
        raise _tts_sidecar_error(resp)
    return Response(content=resp.content, media_type="audio/wav")
```

Add the error translator beside `_sidecar_error`:

```python
#: Same reasoning as _SIDECAR_CLIENT_CODES, for the synthesis path.
_TTS_CLIENT_CODES = {"text_too_long", "unknown_voice", "text_required"}


def _tts_sidecar_error(resp: httpx.Response) -> ApiError:
    if 400 <= resp.status_code < 500:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        if code in _TTS_CLIENT_CODES:
            message = (body.get("message") if isinstance(body, dict) else None) or code
            return ApiError(resp.status_code, code, str(message))
    return ApiError(502, "tts_failed", f"Voice service error {resp.status_code}")
```

Add `Body` and `Response` to the FastAPI imports.

- [ ] **Step 5: Run to verify they pass**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_voice.py -q`
Expected: PASS — all voice tests including the 5 new ones.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest -q`
Expected: **397 passed** (392 after Task 3 + 5 new). `probe_sidecar`'s signature changed, so any existing test that calls it directly must be updated to unpack a tuple — check `tests/test_voice.py` for those.

- [ ] **Step 7: Commit**

```bash
git add backend/app/voice/routes.py backend/app/config.py backend/app/main.py backend/tests/test_voice.py
git commit -m "feat(voice): authenticated /api/voice/speak proxy and tts capability"
```

---

### Task 5: Frontend — synthesize through our API

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/types/api.ts`, `frontend/src/components/chat/MessageBubble.tsx`
- Rewrite: `frontend/src/hooks/useVoiceSynthesis.ts`

**Interfaces:**
- Consumes: `POST /api/voice/speak`, `PATCH /api/auth/me`, `GET /api/voice/capabilities` (Tasks 3–4).
- Produces: `api.speakText(text: string): Promise<Blob>`, `api.updateMe(payload: {voice: string}): Promise<User>`, `User.voice: string`, and `useVoiceSynthesis()` keeping its existing return shape `{ speak, stop, isSpeaking, isSupported }`. Task 6 calls `api.updateMe` and `api.speakText`.

- [ ] **Step 1: API client and types**

In `frontend/src/types/api.ts`, add `voice: string;` to the `User` interface after `isAdmin`.

In `frontend/src/lib/api.ts`, change the capability type and add two functions in the Voice section:

```ts
export const getVoiceCapabilities = (): Promise<{ stt: boolean; tts: boolean }> =>
  req("/api/voice/capabilities", { headers: authHeaders() }).then(r => r.json());

export async function speakText(text: string): Promise<Blob> {
  const res = await req("/api/voice/speak", {
    method: "POST",
    body: JSON.stringify({ text }),
    headers: { ...JSON_H, ...authHeaders() },
  });
  return res.blob();
}

export const updateMe = (payload: { voice: string }): Promise<User> =>
  req("/api/auth/me", {
    method: "PATCH",
    body: JSON.stringify(payload),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());
```

- [ ] **Step 2: Rewrite the hook**

Replace the whole of `frontend/src/hooks/useVoiceSynthesis.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";

import { speakText } from "@/lib/api";

/**
 * Read an answer aloud using our own TTS sidecar.
 *
 * Replaces the previous `window.speechSynthesis` implementation, which could
 * not work on the air-gapped deployment and gave every user whatever voice
 * their OS happened to ship. There is deliberately no browser fallback:
 * falling back would reintroduce exactly the inconsistency this removes.
 *
 * The exported shape is unchanged, so MessageBubble needs no structural edit.
 */
export function useVoiceSynthesis() {
  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  // Bumped on every stop/new request so a slow synthesis that resolves after
  // the user moved on does not start playing over the top.
  const runRef = useRef(0);

  const isSupported = typeof window !== "undefined" && typeof Audio !== "undefined";

  const stop = useCallback(() => {
    runRef.current += 1;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setIsSpeaking(false);
  }, []);

  const speak = useCallback(
    async (text: string) => {
      if (!isSupported || !text.trim()) return;
      stop();
      const run = runRef.current;
      // Strip markdown so the engine reads prose, not syntax.
      const clean = text
        .replace(/```[\s\S]*?```/g, " code block ")
        .replace(/[*_`#>|]/g, "")
        .replace(/\[(.*?)\]\(.*?\)/g, "$1");
      setIsSpeaking(true);
      try {
        const blob = await speakText(clean);
        if (runRef.current !== run) return; // stopped while synthesizing
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => {
          if (runRef.current === run) stop();
        };
        audio.onerror = () => {
          if (runRef.current === run) stop();
        };
        await audio.play();
      } catch {
        if (runRef.current === run) stop();
      }
    },
    [isSupported, stop],
  );

  useEffect(() => stop, [stop]);

  return { isSpeaking, speak, stop, isSupported };
}
```

- [ ] **Step 3: Gate the read button**

In `frontend/src/components/chat/MessageBubble.tsx`, import the capability query alongside the existing hook:

```ts
import { useVoiceCapabilities } from "@/lib/queries";
```

and after the existing `useVoiceSynthesis()` call (around line 64):

```ts
  const { data: voiceCaps } = useVoiceCapabilities();
  const canRead = isSupported && !!voiceCaps?.tts;
```

Then change the condition that renders the read-aloud button so it uses `canRead` instead of `isSupported`. Read the surrounding JSX and make the minimal edit — do not restructure the component.

- [ ] **Step 4: Typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: both clean. There is no frontend test harness in this project, so this is the whole automated gate.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/types/api.ts \
        frontend/src/hooks/useVoiceSynthesis.ts frontend/src/components/chat/MessageBubble.tsx
git commit -m "feat(voice): read aloud through our own TTS instead of Web Speech"
```

---

### Task 6: Frontend — the voice picker

**Files:**
- Create: `frontend/src/components/layout/VoiceDialog.tsx`
- Modify: `frontend/src/components/layout/ProfileFooter.tsx`

**Interfaces:**
- Consumes: `api.updateMe`, `api.speakText` (Task 5); the Zustand auth store at `@/stores/authStore` with `setAuth(user, accessToken)`.

Background: `ProfileFooter.tsx` already imports `Dialog`/`DialogContent`/`DialogHeader`/`DialogTitle`/`DialogFooter` and `DropdownMenu`/`DropdownMenuItem`, and already manages a dialog with `const [changePwOpen, setChangePwOpen] = useState(false)` opened from a `DropdownMenuItem onSelect={…}`. Follow that pattern exactly.

- [ ] **Step 1: Create the dialog**

Create `frontend/src/components/layout/VoiceDialog.tsx`:

```tsx
import { useState } from "react";
import { Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { speakText, updateMe } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";

const VOICES = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"];

/** One short line of Indonesian, so previews are comparable across voices. */
const PREVIEW = "Halo, saya Citya. Ada yang bisa saya bantu?";

export function VoiceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const user = useAuthStore((s) => s.user);
  const accessToken = useAuthStore((s) => s.accessToken);
  const setAuth = useAuthStore((s) => s.setAuth);
  const [selected, setSelected] = useState(user?.voice ?? "F2");
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const preview = async (voice: string) => {
    setPreviewing(voice);
    setError(null);
    try {
      // Preview always plays the *saved* voice server-side, so save first if
      // the selection changed -- the endpoint deliberately takes no voice
      // parameter (it is server-controlled scope).
      if (voice !== user?.voice) {
        const updated = await updateMe({ voice });
        if (accessToken) setAuth(updated, accessToken);
      }
      const blob = await speakText(PREVIEW);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch {
      setError("Could not play a preview.");
    } finally {
      setPreviewing(null);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateMe({ voice: selected });
      if (accessToken) setAuth(updated, accessToken);
      onOpenChange(false);
    } catch {
      setError("Could not save the voice.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Voice</DialogTitle>
          <DialogDescription>
            The voice used when reading answers aloud.
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-2">
          {VOICES.map((v) => (
            <div
              key={v}
              className={cn(
                "flex items-center justify-between rounded-md border px-3 py-2",
                selected === v && "border-brand-red",
              )}
            >
              <button
                type="button"
                className="flex-1 text-left text-sm"
                onClick={() => setSelected(v)}
                aria-pressed={selected === v}
              >
                {v}
              </button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Preview voice ${v}`}
                disabled={previewing !== null}
                onClick={() => preview(v)}
              >
                {previewing === v ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Play className="size-4" />
                )}
              </Button>
            </div>
          ))}
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
        <DialogFooter>
          <Button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Wire it into the profile menu**

In `frontend/src/components/layout/ProfileFooter.tsx`:

```tsx
import { VoiceDialog } from "@/components/layout/VoiceDialog";
```

add state beside `changePwOpen`:

```tsx
  const [voiceOpen, setVoiceOpen] = useState(false);
```

add a menu item immediately before the "Change password" `DropdownMenuItem`:

```tsx
          <DropdownMenuItem onSelect={() => setVoiceOpen(true)}>
            Voice…
          </DropdownMenuItem>
```

and render the dialog beside the existing change-password `Dialog`:

```tsx
      <VoiceDialog open={voiceOpen} onOpenChange={setVoiceOpen} />
```

Match the icon convention of the neighbouring items — if the existing items render a lucide icon before their label, add one here too (`Volume2` from `lucide-react`).

- [ ] **Step 3: Typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/VoiceDialog.tsx frontend/src/components/layout/ProfileFooter.tsx
git commit -m "feat(voice): voice picker in the profile menu"
```

---

### Task 7: Air-gapped provisioning and the runbook

**Files:**
- Create: `backend/scripts/setup_tts_models.py`
- Modify: `backend/.env.prod.example`, `docs/DEPLOY.md`

- [ ] **Step 1: Write the provisioning script**

Create `backend/scripts/setup_tts_models.py`:

```python
#!/usr/bin/env python
"""Provision Supertonic 3 assets for an air-gapped host (voice §1b).

Mirrors setup_ocr_models.py. On a connected machine:

    env -u VIRTUAL_ENV uv run --with huggingface-hub \
        python scripts/setup_tts_models.py --dest ../tts_models

Then copy that directory to the target and set TTS_MODEL_DIR to it.

`--manifest` prints the file list without downloading, for hosts that must
fetch through something other than this script.
"""
from __future__ import annotations

import argparse
from pathlib import Path

REPO = "Supertone/supertonic-3"
FILES = [
    "config.json",
    "onnx/text_encoder.onnx",
    "onnx/duration_predictor.onnx",
    "onnx/vector_estimator.onnx",
    "onnx/vocoder.onnx",
    "onnx/tts.json",
    "onnx/unicode_indexer.json",
] + [f"voice_styles/{v}.json" for v in
     ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=Path("../tts_models"))
    ap.add_argument("--manifest", action="store_true",
                    help="print the URLs instead of downloading")
    args = ap.parse_args()

    if args.manifest:
        for f in FILES:
            print(f"https://huggingface.co/{REPO}/resolve/main/{f}")
        return

    from huggingface_hub import hf_hub_download

    args.dest.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        path = hf_hub_download(repo_id=REPO, filename=f, local_dir=str(args.dest))
        print(f"ok  {f}  -> {path}")
    print(f"\nSet TTS_MODEL_DIR={args.dest.resolve()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it works from a COLD cache**

This is the step the spec flags as unproven — during the probe the model was already cached, so the download path has never run.

```bash
cd backend
mv ~/.cache/supertonic3 ~/.cache/supertonic3.bak   # hide the existing copy
env -u VIRTUAL_ENV uv run --with huggingface-hub \
    python scripts/setup_tts_models.py --dest /tmp/tts_models_test
du -sh /tmp/tts_models_test        # expect ~385 MB
env -u VIRTUAL_ENV uv run --with supertonic python -c "
from supertonic import TTS
t = TTS('/tmp/tts_models_test')
s = t.get_voice_style(voice_name='F2')
wav, d = t.synthesize(text='Dokumen ditemukan.', lang='id', voice_style=s, total_steps=8, speed=1.0)
print('synthesized', d, 'seconds from a cold-provisioned directory')
"
mv ~/.cache/supertonic3.bak ~/.cache/supertonic3   # restore
```

If loading from `--dest` fails, the directory layout `hf_hub_download` produced does not match what `TTS(path)` expects — fix the script (most likely by passing `local_dir_use_symlinks=False` or flattening a nested `snapshots/` path) until this command synthesizes. **Do not mark this step done on a warning.**

- [ ] **Step 3: Env example**

Append to `backend/.env.prod.example`:

```
# --- Voice / TTS (§1b): read-aloud, same sidecar as STT (port 8002).
#     The read button stays hidden unless the sidecar reports tts on /health.
# Sidecar-side knobs (set in the voice service's own environment):
#   TTS_ENGINE=supertonic
#   TTS_MODEL_DIR=            # air-gapped: dir from scripts/setup_tts_models.py
#   TTS_STEPS=8               # quality 5-12; 8 is the tested default
#   TTS_SPEED=1.0
#   TTS_MAX_CHARS=5000
MAX_TTS_CHARS=5000
```

- [ ] **Step 4: DEPLOY.md**

In `docs/DEPLOY.md` §3h (the voice sidecar section), after the STT model provisioning text, add a "TTS models" subsection stating: the assets are ~385 MB (`vector_estimator.onnx` alone is 257 MB), provisioned with `scripts/setup_tts_models.py`, pointed at by `TTS_MODEL_DIR`; and that `/health` reports a `tts` object naming the engine and the ten voices, so a sidecar with STT but no TTS models is visible there rather than as a broken button.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/setup_tts_models.py backend/.env.prod.example docs/DEPLOY.md
git commit -m "docs(voice): TTS model provisioning and runbook"
```

---

### Task 8: Verification

- [ ] **Step 1:** `cd backend/voice && env -u VIRTUAL_ENV uv run pytest tests/ -q` — **31 passed**.
- [ ] **Step 2:** `cd backend && env -u VIRTUAL_ENV uv run pytest -q` — **397 passed**, 0 failures.
- [ ] **Step 3:** `cd frontend && npm run typecheck && npm run build` — both clean.
- [ ] **Step 4 (manual):** start the sidecar (`cd backend/voice && env -u VIRTUAL_ENV uv run uvicorn voice.service.main:app --app-dir .. --port 8002`) and the backend with `VOICE_SERVICE_URL=http://localhost:8002`. Confirm `curl localhost:8002/health` reports a `tts` object listing ten voices.
- [ ] **Step 5 (manual):** in the app, read an assistant answer aloud. Confirm audio plays, the button shows a speaking state, and pressing stop halts playback immediately.
- [ ] **Step 6 (manual):** open Voice… from the profile menu, preview two voices, save a non-default one, reload, and confirm it persisted.
- [ ] **Step 7 (manual):** read an answer containing a rupiah figure and an `RT 03`-style address. Confirm the amount and the address are spoken correctly — this is the failure the whole normalization layer exists to prevent.
- [ ] **Step 8 (manual):** stop the sidecar, reload, and confirm the read button disappears rather than erroring.
- [ ] **Step 9:** `git log --oneline main..HEAD` — spec, plan, and the task commits.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2 engine choice (Supertonic) | Task 1 Steps 1, 4 |
| §2 finding 1: normalization fixes numbers | Task 1 Steps 2, 4; Task 8 Step 7 |
| §2 finding 2: normalization is engine-specific | Task 1 Step 4 (inside the adapter) |
| §3.1 extend the sidecar, one service | Tasks 1–2 |
| §3.2 proxy, capability flag, text cap | Task 4 |
| §3.3 per-user voice, default F2 | Task 3 |
| §3.4 whitelist in both places | Task 3 Step 5 (backend), Task 1 Step 4 + Task 2 Step 4 (sidecar) |
| §3.5 hook rewrite, gating, Web Speech removed | Task 5 |
| §3.5 voice picker in profile dropdown | Task 6 |
| §3.6 air-gapped provisioning + cold-cache gap | Task 7 Steps 1–2 |
| §4 tests | Tasks 1–5 |
| §5 out of scope | No tasks, by design |

**Type consistency:** `Synthesizer.synthesize(text, voice) -> tuple[np.ndarray, int]` is defined in Task 1, faked in Task 2's conftest with the same signature, and called in Task 2's endpoint. `VOICES`/`DEFAULT_VOICE` are defined in Task 1, mirrored in the backend in Task 3 Step 5 (with a comment saying why it is a mirror and not an import), and mirrored again in the frontend in Task 6. `User.voice: str` (Task 3) matches `User.voice: string` (Task 5 Step 1). `api.speakText -> Promise<Blob>` and `api.updateMe -> Promise<User>` (Task 5) are the exact names Task 6 imports.

**Known risk, called out rather than hidden:** Task 7 Step 2 is the only step whose outcome I cannot predict — the `hf_hub_download` layout may not match what `TTS(path)` expects, and the step says explicitly not to mark it done on a warning.
