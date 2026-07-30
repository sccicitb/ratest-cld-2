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

        # A copied-in air-gapped model directory (STT_MODEL_DIR) takes precedence
        # over the model name -- but /health should still report *something*
        # meaningful, so surface the path an operator can recognize.
        model_or_path = s.model_dir or s.model
        self.model = model_or_path
        self.device = device
        self.vad = s.vad
        self.beam_size = s.beam_size
        self._model = WhisperModel(model_or_path, device=device, compute_type=compute)

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
