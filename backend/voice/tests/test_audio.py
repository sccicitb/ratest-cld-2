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
