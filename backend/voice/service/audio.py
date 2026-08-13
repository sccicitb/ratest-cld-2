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
