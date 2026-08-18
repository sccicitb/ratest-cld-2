"""The /synthesize endpoint (§1b)."""
from __future__ import annotations

import io
import wave


def test_health_reports_tts_alongside_stt(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["tts"]["engine"] == "fake-tts"
    assert "F2" in body["tts"]["voices"]


def test_synthesize_returns_wav_bytes(client, fake_tts):
    resp = client.post("/synthesize", json={"text": "Dokumen ditemukan.", "voice": "F2"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(resp.content), "rb") as wf:
        assert wf.getframerate() == 44_100
        assert wf.getnframes() > 0
    assert fake_tts.calls == [("Dokumen ditemukan.", "F2")]


def test_synthesize_hands_the_engine_raw_text(client, fake_tts):
    """The endpoint must NOT normalize -- the adapter does.

    Normalization is engine-specific: the same expansion improved Supertonic
    (numbers 21.1% -> 4.5%) and made Piper worse (4.5% -> 8.9%), because Piper
    ships its own front-end. Normalizing here would apply it to every future
    engine and bake in that regression. `tts_normalize` itself is covered in
    tests/test_tts.py; this pins where it is allowed to run.
    """
    client.post("/synthesize", json={"text": "Rp 2,3 miliar", "voice": "F2"})
    spoken, _voice = fake_tts.calls[0]
    assert spoken == "Rp 2,3 miliar"


def test_synthesize_rejects_an_unknown_voice(client, fake_tts):
    """A voice name resolves to a filesystem path inside the engine, so an
    unknown one must be refused here rather than handed on."""
    resp = client.post("/synthesize", json={"text": "halo", "voice": "../../etc/passwd"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "unknown_voice"
    assert fake_tts.calls == []


def test_synthesize_rejects_text_over_the_cap(client, fake_tts):
    from voice.service.config import settings

    resp = client.post(
        "/synthesize", json={"text": "a" * (settings.tts_max_chars + 1), "voice": "F2"}
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "text_too_long"
    assert fake_tts.calls == []


def test_synthesize_defaults_the_voice_when_omitted(client, fake_tts):
    resp = client.post("/synthesize", json={"text": "halo"})
    assert resp.status_code == 200
    assert fake_tts.calls[0][1] == "F2"
