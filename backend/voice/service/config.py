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
