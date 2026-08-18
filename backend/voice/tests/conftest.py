"""Fixtures — a fake engine so tests never touch model weights."""
from __future__ import annotations

import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice.service import main as service_main


class FakeTranscriber:
    """Records what it was handed; returns a canned transcript.

    Also stands in for a *slow* engine: `gate` blocks inside transcribe() so a
    test can hold a transcription in flight and observe the service's behaviour
    while it runs. `max_overlap` records the high-water mark of concurrent
    transcribe() calls -- the real WhisperModel is not safe above 1.
    """

    name = "fake"
    model = "fake-model"
    device = "cpu"

    def __init__(self, text: str = "halo dunia") -> None:
        self.text = text
        self.calls: list[tuple[int, str | None]] = []
        # Set by a test to block inside transcribe(); `entered` fires first.
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_overlap = 0

    def transcribe(self, samples: np.ndarray, language: str | None) -> str:
        with self._lock:
            self.calls.append((len(samples), language))
            self._in_flight += 1
            self.max_overlap = max(self.max_overlap, self._in_flight)
        try:
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(10), "test never released the engine gate"
            return self.text
        finally:
            with self._lock:
                self._in_flight -= 1


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


@pytest.fixture()
def fake() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture()
def client(
    fake: FakeTranscriber, fake_tts: FakeSynthesizer, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    # lifespan() calls build_transcriber(settings) directly -- a plain function
    # call, not a Depends() -- so overriding get_transcriber alone leaves the
    # real engine construction on the table when TestClient(...) enters and
    # runs startup. Patch build_transcriber itself so lifespan gets the fake
    # too; no real engine is ever built, so no weights are ever fetched.
    monkeypatch.setattr(service_main, "build_transcriber", lambda settings: fake)
    # Kept as well: guards route-level Depends(get_transcriber) resolution
    # directly, independent of what lifespan did.
    service_main.app.dependency_overrides[service_main.get_transcriber] = lambda: fake
    # Same reasoning as build_transcriber: lifespan calls build_synthesizer
    # directly, so patching the dependency alone would still download and
    # load a real 385 MB model at TestClient startup.
    monkeypatch.setattr(service_main, "build_synthesizer", lambda settings: fake_tts)
    service_main.app.dependency_overrides[service_main.get_synthesizer] = lambda: fake_tts
    with TestClient(service_main.app) as c:
        yield c
    service_main.app.dependency_overrides.clear()
