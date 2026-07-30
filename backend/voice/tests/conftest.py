"""Fixtures — a fake engine so tests never touch model weights."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice.service import main as service_main


class FakeTranscriber:
    """Records what it was handed; returns a canned transcript."""

    name = "fake"
    model = "fake-model"
    device = "cpu"

    def __init__(self, text: str = "halo dunia") -> None:
        self.text = text
        self.calls: list[tuple[int, str | None]] = []

    def transcribe(self, samples: np.ndarray, language: str | None) -> str:
        self.calls.append((len(samples), language))
        return self.text


@pytest.fixture()
def fake() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture()
def client(fake: FakeTranscriber) -> TestClient:
    service_main.app.dependency_overrides[service_main.get_transcriber] = lambda: fake
    with TestClient(service_main.app) as c:
        yield c
    service_main.app.dependency_overrides.clear()
