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
        # Air-gapped hosts: a directory holding the converted CT2 model, copied in
        # by hand (DEPLOY.md 3h). Takes precedence over `model` when set.
        self.model_dir: str = os.environ.get("STT_MODEL_DIR", "")
        # Spec §4.1. The backend's byte cap cannot enforce this: Opus at ~32 kbps
        # only reaches 10 MB at roughly 40 minutes, so a mic left on passes the
        # edge check and lands on the GPU. The sidecar is the only component that
        # knows the real duration -- it has decoded the audio.
        self.max_audio_seconds: float = float(
            os.environ.get("STT_MAX_AUDIO_SECONDS", "120")
        )

        # --- TTS (§1b) ---
        self.tts_engine: str = os.environ.get("TTS_ENGINE", "supertonic")
        self.tts_model_dir: str = os.environ.get("TTS_MODEL_DIR", "")
        self.tts_steps: int = int(os.environ.get("TTS_STEPS", "8"))
        self.tts_speed: float = float(os.environ.get("TTS_SPEED", "1.0"))
        # A very long answer is a multi-second GPU hold at RTF ~0.2. The
        # sidecar is the only component that knows the real cost.
        self.tts_max_chars: int = int(os.environ.get("TTS_MAX_CHARS", "5000"))


settings = Settings()
