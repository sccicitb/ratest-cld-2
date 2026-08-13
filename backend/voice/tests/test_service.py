"""The sidecar's wire contract."""
from __future__ import annotations

from voice.service.config import settings
from voice.tests.test_audio import _sine_wav


def test_health_reports_what_actually_loaded(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    # Deploys need to verify the running model, not the intended one.
    assert body["model"] == "fake-model"
    assert body["engine"] == "fake"


def test_transcribe_returns_text_and_duration(client, fake):
    resp = client.post(
        "/transcribe", files={"audio": ("clip.wav", _sine_wav(1.0), "audio/wav")}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "halo dunia"
    assert 900 < body["durationMs"] < 1100
    assert body["model"] == "fake-model"


def test_transcribe_passes_language_through(client, fake):
    client.post(
        "/transcribe",
        files={"audio": ("clip.wav", _sine_wav(0.5), "audio/wav")},
        data={"language": "id"},
    )

    assert fake.calls[0][1] == "id"


def test_transcribe_defaults_language_from_settings(client, fake):
    client.post("/transcribe", files={"audio": ("c.wav", _sine_wav(0.5), "audio/wav")})

    assert fake.calls[0][1] == "id"  # config.py default


def test_undecodable_audio_is_400_not_500(client):
    resp = client.post(
        "/transcribe", files={"audio": ("x.webm", b"garbage", "audio/webm")}
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "audio_undecodable"


def test_over_long_audio_is_413_audio_too_long(client, fake, monkeypatch):
    """The byte cap at the backend edge cannot catch this: Opus at ~32 kbps only
    hits 10 MB at ~40 minutes. Duration is knowable only after decoding, here."""
    monkeypatch.setattr(settings, "max_audio_seconds", 0.5)

    resp = client.post(
        "/transcribe", files={"audio": ("clip.wav", _sine_wav(1.0), "audio/wav")}
    )

    assert resp.status_code == 413
    assert resp.json()["code"] == "audio_too_long"
    # Rejected *before* the slot, so an over-long clip never occupies the GPU.
    assert fake.calls == []


def test_audio_at_the_limit_is_accepted(client, monkeypatch):
    """Boundary: the check is `>`, not `>=` -- exactly-at-the-cap must pass."""
    monkeypatch.setattr(settings, "max_audio_seconds", 1.5)

    resp = client.post(
        "/transcribe", files={"audio": ("clip.wav", _sine_wav(1.0), "audio/wav")}
    )

    assert resp.status_code == 200


def test_default_duration_cap_is_the_spec_value(client):
    """Spec §4.1: 120 seconds. A default of 0 or None would disable the cap."""
    assert settings.max_audio_seconds == 120
