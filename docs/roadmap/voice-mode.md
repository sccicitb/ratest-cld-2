# Roadmap — Voice mode

Status: **planned / not started** (parked 2026-07-27). Captured from a brainstorming session; not a spec yet.

## Goal

Talk to CityA and hear it talk back — ultimately a **real-time streaming voice conversation** (like ChatGPT Advanced Voice), reached in stages.

## Hard constraint that shapes the whole design

Prod is **air-gapped** (Hyper-V Linux VM, no public IP, Cloudflare Tunnel only), so cloud voice APIs (OpenAI Whisper, ElevenLabs, Deepgram) are out. Voice must be **local** — the same move already made for the LLM (llama-server), embeddings (BGE-M3), and OCR (PaddleOCR-ONNX).

- **STT (speech→text):** local **Whisper** (`whisper.cpp` / `faster-whisper`) — handles the Indonesian corpus.
- **TTS (text→speech):** local **Piper** — onnxruntime-based, slots into the runtime already shipped for PDFOxide. Indonesian voices exist.

## The ladder (each step reuses the last — no throwaway plumbing)

**① STT/TTS capability layer (buttons) — the reusable foundation.**
- STT endpoint (Whisper) + TTS endpoint (Piper) + both models on the box.
- Frontend mic capture (`MediaRecorder`, needs HTTPS — have it via Cloudflare) + audio playback.
- Two buttons: **record** (→ transcribe → drops into chat as a normal message; RAG/tools unchanged downstream) and **read** (→ synthesize the finished answer → play).
- Because "read" runs on the *complete* answer, this needs **no streaming/sentence-chunked TTS** — sidesteps the trickiest turn-based nuance.
- Everything here is reused by real-time; nothing is rewritten.

**② Thin end-to-end loop (validation gate, ~free) — not a polished product.**
- Auto-wire the two endpoints: auto-read responses + push-to-talk, so you can actually *talk to CityA end-to-end*.
- Purpose: **feel the latency and the slow-agent problem for real** before committing to the expensive real-time build.
- UX dial (only real choice at ①/②): **manual "read" button** (voice-assisted text chat) vs **auto-read** (hands-free, more "voice-mode"). Neither needs real-time machinery.

**③ Real-time streaming voice — the real project.**
- The big lift: bidirectional audio transport (WebSocket/WebRTC), **streaming** STT (partial transcripts), **VAD**/endpointing, **streaming** TTS, **barge-in** (interrupt), echo cancellation.
- None of these are needed before ③ — they are exactly what separates real-time from turn-based.

## Key open decision (blocks ③, not ①/②)

**The slow-agent tension.** MCP tools run 60–90s; a long silence is tolerable in turn-based (paper over it visually) but **brutal** in real-time voice. So ③ must decide:
- **Fast Q&A path only** — real-time voice targets quick retrieval answers; heavy tool-chains stay in text. (Snappier, recommended for first real-time cut.)
- **Full agent over voice** — voice reaches parity with text incl. slow tool-chains, requiring spoken progress cues / earcons during long runs. (Bigger.)

## Notes

- Thinking (reasoning_content) is for *reading*, not speaking — voice speaks the final answer only.
- Indonesian: Whisper transcribes it well; Piper has Indonesian voices — verify voice quality during ①.
- STT/TTS placement (in-process vs a sidecar like the code-exec sandbox) is an ①-time detail — Whisper's RAM may argue for a sidecar; Piper is tiny.
