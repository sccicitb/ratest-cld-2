# Voice §1b — TTS Capability Layer (read aloud) — Design

**Date:** 2026-08-18
**Status:** approved (design), not yet implemented
**Follows:** `2026-07-29-voice-stt-capability-design.md` (§1a, shipped 2026-08-13)
**Touches:** BACKEND_SPEC.md (new voice section), a users-table migration

## 1. Problem

`frontend/src/hooks/useVoiceSynthesis.ts` reads answers aloud today with
`window.speechSynthesis` — the browser's Web Speech API. It has the same defect
the mic had before §1a:

- **It cannot work air-gapped.** Browser TTS quality and voice availability come
  from the client OS, and on the deployment target there is no guarantee an
  Indonesian voice exists at all.
- **Quality is outside our control.** Whatever the client ships is what the user
  hears; two users get different voices for the same answer.

§1b replaces it with a local engine, the same way §1a replaced the Web Speech
mic. This is a *replacement*, not a greenfield feature: the read button already
exists in `MessageBubble.tsx` (`Volume2` / "Read aloud"). Only what sits behind
`speak()` changes.

## 2. Engine decision — Supertonic 3

Decided by measurement, not vendor claims. `backend/scripts/check_tts_pipeline.py`
synthesized 12 Indonesian texts per engine, then round-tripped every wav through
the §1a faster-whisper sidecar and scored WER against the source.

| run | numbers | longform | codeswitch | short | mean WER |
|---|---|---|---|---|---|
| piper | 4.5% | 2.1% | 8.3% | 0.0% | 3.7% |
| piper + normalization | 8.9% | 1.5% | 8.3% | 0.0% | 4.7% |
| supertonic | 21.1% | 2.5% | 2.8% | 0.0% | 6.6% |
| **supertonic + normalization** | **4.5%** | 2.5% | **2.8%** | 0.0% | **2.5%** |

Supertonic 3: 99M params, 31 languages including `id`, ONNX Runtime, 385 MB on
disk, RTF ~0.195 (≈5× realtime) on an M1 CPU. Model licence OpenRAIL-M, sample
code MIT — the user has confirmed this is acceptable for this deployment.

**Why not Piper**, which won the raw comparison: Piper wins throughput
(RTF 0.031) and has native streaming, but ships one fixed voice per model file.
The ultimate objective is §3 real-time conversation, where expressiveness is the
product; Supertonic ships ten voice styles plus zero-shot cloning, and beats
Piper 3× on code-switching (2.8% vs 8.3%) — English technical loanwords are
common in this corpus.

**Two findings that shape the design:**

1. **Supertonic's 21% number failure was a missing text front-end, not a broken
   model.** Raw, it read `Rp 875.000.000` as "2775 ribu" and `RT 03 RW 07` as
   "RT NOA 3 RW Loara Juju". With digits and `Rp` expanded first, numbers fell
   to 4.5% and the RT/RW mangling disappeared — it was the *leading zero*, fixed
   by speaking `03` as "nol tiga" rather than "tiga".
2. **The same normalization made Piper worse** (numbers 4.5% → 8.9%), because
   Piper ships its own front-end and pre-spelling fights it. Normalization is
   therefore **engine-specific** and must live *behind* the engine seam, not in
   front of it.

## 3. Design

### 3.1 Extend the existing sidecar — do not add a second one

`backend/voice/` already exists for §1a. Supertonic requires
`onnxruntime>=1.19.0`; the sidecar pins `1.23.2`, and Supertonic resolves
against exactly that. `numpy>=1.21.0` vs the sidecar's `>=1.26` is likewise
compatible. Verified by resolving both in one environment.

The dependency-isolation argument that justified a sidecar in the first place
does not justify a *second* sidecar here, and one service means one `/health`,
one capability probe, one thing to install and run under NSSM.

New module `backend/voice/service/tts.py`, mirroring `engines.py`:

```python
class Synthesizer(Protocol):
    name: str
    model: str
    voices: list[str]

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]: ...
```

`SupertonicSynthesizer` implements it and **owns its own text normalization** —
the `tts_normalize` logic proven in the probe (expand digits and `Rp`, preserve
punctuation and case, spell leading zeros digit by digit). A future Piper
adapter would simply not normalize. This is the direct consequence of §2's
finding 2.

New endpoint `POST /synthesize` on the sidecar: `{text, voice}` → `audio/wav`.

### 3.2 Backend proxy and capability flag

`POST /api/voice/speak`, authenticated, mirroring `/api/voice/transcribe`:
accepts `{text}`, resolves the voice from the **authenticated user**, forwards
to the sidecar, streams the wav back.

`GET /api/voice/capabilities` currently returns `{"stt": bool}`. It gains
`{"tts": bool}`, from the same startup `/health` probe — the sidecar reports
which capabilities actually loaded, so a deployment with STT but no TTS models
reports `tts: false` and the read button stays hidden rather than erroring.

Text length is capped (`MAX_TTS_CHARS`, default 5000). At RTF 0.2 a very long
answer is a multi-second GPU hold, and the sidecar is the only component that
knows the real cost — the same reasoning as §1a's `STT_MAX_AUDIO_SECONDS`.

### 3.3 Voice is a per-user preference

New column on `users`:

```python
voice: Mapped[str] = mapped_column(String, default="F2", server_default="F2")
```

`server_default` means existing rows need no backfill. Surfaced on `UserOut`;
changed through `PATCH /api/auth/me` with `{voice}`.

Chosen over a JSON `preferences` blob: the table is flat columns today
(`display_name`, `avatar_url`, `is_admin`, `disabled`) and one preference does
not justify inventing a parallel mechanism. §2 will add auto-read and speed;
three columns is still cleaner than a blob, and past four this decision should
be revisited.

**Default `F2`** — one of five voices that scored 0.0% WER in the ten-voice
sweep. Recorded as chosen on the numbers; the user had not confirmed by ear at
the time of writing.

### 3.4 Voice names are untrusted input — whitelist them

This is the one genuinely new security surface in §1b. Supertonic resolves
`get_voice_style(voice_name=X)` against `voice_styles/<X>.json`, so a
user-supplied string flows toward a filesystem path.

- The backend validates against the ten known styles (`M1`–`M5`, `F1`–`F5`) on
  write, rejecting anything else with a 422 — an invalid value must never reach
  the database, let alone the sidecar.
- The sidecar validates **independently** against the voices it actually loaded,
  rather than trusting its caller.

Same discipline as `ToolContext` in §7: server-controlled values are never taken
from the client. Validating in two places is deliberate, not redundant — the
sidecar is separately reachable on its own port.

### 3.5 Frontend

- `useVoiceSynthesis.ts` is rewritten: `speak(text)` POSTs to
  `/api/voice/speak`, plays the returned wav via an `Audio` element, and `stop()`
  pauses and revokes the object URL. The hook's exported shape
  (`speak`, `stop`, `isSpeaking`, `isSupported`) is unchanged, so
  `MessageBubble.tsx` needs no structural edit. **All `window.speechSynthesis`
  usage is removed** — leaving a browser fallback would reintroduce exactly the
  inconsistency §1b exists to remove.
- The read button is gated on `capabilities.tts`, the way the mic is gated on
  `capabilities.stt`.
- Markdown stripping stays, and moves server-side is **not** proposed: it is
  presentation logic and the frontend already has it.
- New `VoiceDialog`, opened from a "Voice…" item in `ProfileFooter`'s existing
  dropdown. Lists the ten voices as radio options, each with a preview play
  button that synthesizes one short fixed Indonesian sentence. Saving issues the
  `PATCH` and invalidates the user query.

### 3.6 Air-gapped provisioning

`scripts/setup_tts_models.py`, mirroring `setup_ocr_models.py` and §1a's STT
provisioning: fetch the Supertonic assets (`text_encoder`, `duration_predictor`,
`vector_estimator`, `vocoder`, plus `voice_styles/`) to a directory named by
`TTS_MODEL_DIR`, with a `--manifest` mode listing URLs for a connected machine.

**Known gap:** during the probe the model was already in the local cache from
earlier experimentation, so the download path was never exercised. The
provisioning script must be tested from a cold cache before it is trusted in the
runbook.

## 4. Testing

Sidecar (`backend/voice/tests/`):
1. `tts_normalize` expands `Rp 2,3 miliar` → `dua koma tiga miliar rupiah` —
   the scale word stays attached to the amount.
2. `tts_normalize` renders `RT 03` as `RT nol tiga`, not `RT tiga`.
3. `tts_normalize` leaves punctuation and casing intact (prosody is the point).
4. `/synthesize` returns wav bytes for a valid voice.
5. `/synthesize` rejects an unknown voice with 422 rather than touching the
   filesystem.
6. `/synthesize` rejects text over the cap with `text_too_long`.
7. `/health` reports `tts` alongside `stt`.

Backend (`backend/tests/test_voice.py`):
8. `/api/voice/speak` requires authentication.
9. It forwards the **authenticated user's** stored voice, not a client-supplied
   one.
10. `PATCH /api/auth/me` accepts a valid voice and rejects an invalid one (422).
11. `capabilities` reports `tts` from the probe verdict.
12. A new user defaults to `F2`.

Frontend: typecheck and build (no test harness exists in this project).

Manual: pick a non-default voice, reload, confirm it persists; read a long
answer; stop mid-playback; stop the sidecar and confirm the read button
disappears rather than erroring.

## 5. Out of scope

- **Streaming / chunked synthesis.** Supertonic's Python API is whole-text and
  blocking (`max_chunk_length` is internal text segmentation, not incremental
  output). §1b reads a *finished* answer, so this costs nothing here — but §3
  will need sentence-level chunking written by us, which Piper would have
  provided natively. Recorded as a known, accepted cost of this engine choice.
- Auto-read and push-to-talk (§2).
- Speed and per-message voice overrides.
- Zero-shot voice cloning (Voice Builder).
- Caching synthesized audio. Answers are read once; a cache is speculative.
