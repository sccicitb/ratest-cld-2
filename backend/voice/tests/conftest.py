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
def client(fake: FakeTranscriber, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # lifespan() calls build_transcriber(settings) directly -- a plain function
    # call, not a Depends() -- so overriding get_transcriber alone leaves the
    # real engine construction on the table when TestClient(...) enters and
    # runs startup. Patch build_transcriber itself so lifespan gets the fake
    # too; no real engine is ever built, so no weights are ever fetched.
    monkeypatch.setattr(service_main, "build_transcriber", lambda settings: fake)
    # Kept as well: guards route-level Depends(get_transcriber) resolution
    # directly, independent of what lifespan did.
    service_main.app.dependency_overrides[service_main.get_transcriber] = lambda: fake
    with TestClient(service_main.app) as c:
        yield c
    service_main.app.dependency_overrides.clear()
