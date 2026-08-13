"""FasterWhisperTranscriber must prefer STT_MODEL_DIR over STT_MODEL when set
(the air-gapped fallback, DEPLOY.md 3h) -- and never touch real weights while
proving it. Patch faster_whisper.WhisperModel itself: engines.py imports it
locally (`from faster_whisper import WhisperModel`) inside __init__, so the
patch has to land on the real module attribute, not on `engines`.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from voice.service.engines import FasterWhisperTranscriber


class _RecordingWhisperModel:
    """Records the first positional arg it was constructed with; loads nothing."""

    last_arg: Any = None

    def __init__(self, model_or_path, **kwargs):
        _RecordingWhisperModel.last_arg = model_or_path


def _settings(**overrides: object) -> SimpleNamespace:
    base = dict(
        model="large-v3-turbo",
        model_dir="",
        device="cpu",  # avoid _cuda_available() autodetect entirely
        compute_type="int8",
        vad=True,
        beam_size=5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_model_name_reaches_whispermodel_when_dir_unset(monkeypatch):
    monkeypatch.setattr("faster_whisper.WhisperModel", _RecordingWhisperModel)

    t = FasterWhisperTranscriber(_settings(model_dir=""))

    assert _RecordingWhisperModel.last_arg == "large-v3-turbo"
    assert t.model == "large-v3-turbo"  # /health reports this


def test_model_dir_takes_precedence_when_set(monkeypatch):
    monkeypatch.setattr("faster_whisper.WhisperModel", _RecordingWhisperModel)

    t = FasterWhisperTranscriber(_settings(model_dir="/opt/models/whisper-large-v3"))

    assert _RecordingWhisperModel.last_arg == "/opt/models/whisper-large-v3"
    # /health needs to show which model actually loaded -- the path, here.
    assert t.model == "/opt/models/whisper-large-v3"
