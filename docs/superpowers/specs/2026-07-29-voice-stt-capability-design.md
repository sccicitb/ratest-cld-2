# Voice ①a — STT capability layer (record button)

Status: **design**, 2026-07-29. Roadmap: `docs/roadmap/voice-mode.md` (step ①, STT half).
Evidence: `backend/scripts/check_stt_pipeline.py` on ten real Indonesian clips.

## 1. Problem

You can type at CityA but not talk to it. The end state is a real-time spoken
conversation (③), but every part of that is expensive and one number that
decides its shape — what a full round trip actually costs — doesn't exist yet.

①a is the smallest useful step: **a record button that turns speech into text
in the composer.** It ships value on its own, and every piece of it is reused by
②'s validation loop and ③'s streaming build. Nothing here is throwaway.

## 2. Decision

**faster-whisper (CTranslate2), model configurable, default `large-v3-turbo`,
VAD on, language forced to `id`, running in a voice sidecar.**

Chosen from measurement, not benchmarks. Indonesian eliminated most of the 2026
field on the spot — Parakeet, Moonshine and Voxtral are English/European only —
leaving faster-whisper and Qwen3-ASR. On our audio (mean WER over four
comparable clips):

| | fw-turbo | fw-large-v3 | qwen-0.6b | qwen-1.7b |
|---|---|---|---|---|
| code-switching | 13.0% | **4.3%** | 13.0% | — |
| short command | **0.0%** | **0.0%** | 40.0% | 20.0% |
| numbers/acronyms | 6.7% | 10.0% | **3.3%** | — |
| fast informal | 14.8% | 14.8% | **11.1%** | — |
| **mean** | **8.6%** | **7.3%** | 16.9% | — |

Qwen was included *because* of its native code-switching, and that is exactly
where it disappointed: it rendered `summarize` as "semirai se" and `quarterly`
as "kuartal", while Whisper preserved both as English. It also failed the
domain term `retribusi parkir` ("kaderusi Parkhill" at 0.6B, "redistribusi
parkir" at 1.7B) where both Whisper models scored 0.0%.

Qwen genuinely won on numbers/acronyms (the only engine to get `bersumber`
right) and on fast informal speech. Those wins are real and worth re-testing on
prod hardware, which is precisely why the engine is a config value.

`large-v3-turbo` over `large-v3`: the 0.7-point gap sits inside the noise of a
ten-clip sample, turbo *beat* large-v3 on numbers, and turbo is ~7× faster on
GPU — the property ③ will care about most.

Forcing `language=id` is a **precaution, not a measured win**. Every probe run
used auto-detect and Whisper identified Indonesian correctly on all ten clips;
forcing was tested only on Qwen, where it changed nothing (byte-identical
output). It stays the default because auto-detect on 2–4 second clips is a
documented weak spot and the setting costs nothing — but it is a knob
(`STT_LANGUAGE`, empty = auto), not a finding.

**Caveat carried forward:** ten clips, one speaker, one room. This is a smoke
test. It is strong enough to pick an engine and wrong to quote as a WER figure.

## 3. Goals / Non-goals

**Goals**
- Hold-to-record in the composer; release → transcript appears **as editable text**.
- Runs local and air-gapped, on the prod L40, alongside llama-server and BGE-M3.
- Engine and model swappable by config, without touching the backend's env.
- Degrades cleanly when the sidecar is absent (the 8 GB dev Mac cannot host it).

**Non-goals** (each has its own step)
- TTS / the read button — ①b.
- Auto-send, push-to-talk, auto-read — ②.
- Streaming transcripts, VAD endpointing, barge-in — ③.
- Word timestamps, diarization, speaker ID. Qwen's ForcedAligner is not loaded:
  it only produces timestamps we don't need, and doesn't support Indonesian.

## 4. Architecture — a voice sidecar

```
browser  --MediaRecorder-->  backend /api/voice/transcribe  --httpx-->  voice sidecar
 (webm/opus | mp4/aac)        (auth, limits, errors)         (:8002)     faster-whisper
```

**Why a sidecar** (`backend/voice/service/`, mirroring `backend/sandbox/service/`):

1. **Dependency isolation is the point.** We intend to keep trying bleeding-edge
   engines. CTranslate2, `transformers`, and whatever ships next want mutually
   hostile pins, and the backend env already carries torch (FlagEmbedding),
   onnxruntime and pdf_oxide. A failed experiment must not stop the backend from
   starting.
2. **Restart granularity.** Swapping a model restarts the process holding it.
   In-process that kills live SSE streams and the whole `ChatTurnRegistry`.
3. **Dev survivability.** The dev Mac has 8 GB — 1.7B fp16 was OOM-killed there,
   and even a working model would fight the backend for RAM. A sidecar you
   simply don't start locally beats a stub in the backend.

It runs on the **Windows host**, not the Linux VM: that is where the GPU is.

The sidecar owns a `Transcriber` protocol — `transcribe(samples, language) -> str`
— with one implementation per engine, selected by `STT_ENGINE`. `check_stt_pipeline.py`
already contains working adapters for both families; they move here largely as-is.

### 4.1 Audio handling

`MediaRecorder` emits **webm/opus** on Chrome and **mp4/aac** on Safari. The
sidecar decodes with **PyAV** to float32 mono 16 kHz — the same path the probe
uses, so measured accuracy is the accuracy we ship. No ffmpeg binary required.

Limits enforced at the backend edge, before the audio reaches the sidecar:
`max_audio_bytes` (10 MB) and `max_audio_seconds` (120). A push-to-talk button
left running in a pocket must not become an unbounded upload.

## 5. Routes

**Sidecar** (`backend/voice/service/main.py`, port 8002)
- `POST /transcribe` — multipart `audio` + optional `language` → `{text, language, durationMs, engine, model}`
- `GET /health` — liveness, reports engine/model/device so a deploy can verify what actually loaded

**Backend**
- `POST /api/voice/transcribe` — authenticated, multipart passthrough → `{text, durationMs}`.
  Not session-scoped: transcription has nothing to do with a conversation, and
  keeping it separate means ② and ③ can call it from anywhere.
- `GET /api/voice/capabilities` → `{ stt: boolean }`, true when
  `VOICE_SERVICE_URL` is set **and** the sidecar's `/health` answered at
  startup. The frontend hides the mic button when false — no dead controls on a
  dev Mac. A dedicated endpoint rather than `/api/auth/me`: that returns
  `UserOut`, and service availability is not a property of the user.

Failures follow the existing `{ message, code }` contract:
`stt_unavailable` (503, sidecar down), `audio_too_large` (413),
`audio_too_long` (413), `stt_failed` (502).

## 6. Frontend

`InputBar` gains a mic button left of send.

States: **idle** → **recording** (elapsed timer, button becomes stop) →
**transcribing** (spinner) → **text in the composer**.

**The transcript lands in the composer as editable text — it is never sent
automatically.** At 5–15% WER on real speech that is not a courtesy, it is the
difference between a usable feature and one that fires wrong questions at the
model. Auto-send is ②'s experiment, behind a flag, once we know the error rate
in practice.

If the composer already has text, the transcript is appended, not replaced.

Permission handling: `getUserMedia` rejection shows "microphone blocked" with a
hint, and the button stays disabled for the session. `MediaRecorder` needs a
secure context — satisfied in prod by the Cloudflare Tunnel and in dev by
`localhost`.

## 7. Data flow

1. User presses mic → `getUserMedia` → `MediaRecorder.start()`.
2. Release → `stop()` → one `Blob`.
3. `POST /api/voice/transcribe` (multipart).
4. Backend checks auth and limits, forwards to the sidecar via `httpx`
   (following `app/tools/builtin/execute_code.py`: `TimeoutException` and
   `RequestError` become typed errors, never a raw 500).
5. Sidecar decodes → `Transcriber.transcribe()` → text.
6. Text is inserted into the composer. The user edits and sends. **From here the
   existing chat pipeline is untouched** — it is an ordinary message, so RAG,
   tools and attachments all behave exactly as they do today.

## 8. Testing

- **Sidecar unit tests** (`backend/voice/tests/`): a `FakeTranscriber` covers
  the route contract, limits, and the decode path against a checked-in 1-second
  WAV. No model weights in CI.
- **Backend route tests**: stub the sidecar with `httpx.MockTransport` — auth
  required, limits enforced, `stt_unavailable` when the sidecar is down, the
  capability flag reflecting `/health`.
- **Accuracy stays with the probe.** `check_stt_pipeline.py` is the harness for
  engine comparison; it is not a unit test and does not run in CI.
- **Frontend has no test framework** — the mic button is verified manually, and
  this is the third feature in a row where that is true. Adding vitest is a
  separate decision, noted here because it keeps recurring.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Air-gapped prod cannot pull weights from HuggingFace | `scripts/setup_stt_model.py`, mirroring `setup_ocr_models.py`: prefetch the CT2 model, `--manifest` for offline transfer. Provision once per deploy. |
| Fast informal speech is ~15% WER | Transcript is editable before send. Do not auto-send in ①a. |
| Silence produces `". . . ."` | VAD on (`vad_filter=True`) — verified to return empty on our room-tone clip. |
| Sidecar down → dead mic button | Capability flag hides it; `stt_unavailable` if raced. |
| Whisper mishears domain terms (`retribusi`) | Accepted for ①a; `initial_prompt` seeding with domain vocabulary is a cheap future lever. |
| A second process to operate on Windows | NSSM service alongside the backend, documented in DEPLOY.md §3 with the same shape as the existing entries. |

## 10. Follow-ups (not built here)

- ①b — TTS / read button. Piper has an Indonesian voice (`id_ID-news_tts-medium`);
  Chatterbox is MIT and higher quality but its Indonesian support is still an
  open upstream issue (#506, Malay placeholder sample).
- Re-run the probe on the L40: latency, RTF and VRAM from the dev Mac are
  meaningless, and Qwen deserves a fair rematch where 8 GB isn't the constraint.
- Fix `peak_rss_mb` in the probe — it samples RSS at the end rather than tracking
  a maximum, which is why it reported 32 MB for a 1.7B model.
- `initial_prompt` domain vocabulary (`retribusi`, `SIPD`, `kelurahan`, …).
