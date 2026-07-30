"""Backend voice proxy: auth, limits, and honest failure when the sidecar is down."""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.main import app
from app.voice import routes as voice_routes


@pytest.fixture()
def sidecar_ok(monkeypatch):
    """Stub the sidecar with a transport that records the forwarded request."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body_len"] = len(request.content)
        return httpx.Response(
            200, json={"text": "halo dunia", "durationMs": 1200,
                       "language": "id", "engine": "fake", "model": "m"}
        )

    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    app.dependency_overrides[voice_routes.get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    yield seen
    app.dependency_overrides.pop(voice_routes.get_http_client, None)


def test_transcribe_requires_auth(client):
    resp = client.post("/api/voice/transcribe",
                       files={"audio": ("c.webm", b"xx", "audio/webm")})
    assert resp.status_code == 401


def test_transcribe_proxies_and_returns_text(client, auth_headers, sidecar_ok):
    resp = client.post(
        "/api/voice/transcribe",
        files={"audio": ("c.webm", b"pretend-audio", "audio/webm")},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["text"] == "halo dunia"
    assert resp.json()["durationMs"] == 1200
    assert sidecar_ok["url"].endswith("/transcribe")


def test_oversized_audio_rejected_before_the_sidecar(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    monkeypatch.setattr(settings, "max_audio_bytes", 16)

    resp = client.post(
        "/api/voice/transcribe",
        files={"audio": ("c.webm", b"x" * 64, "audio/webm")},
        headers=auth_headers,
    )

    assert resp.status_code == 413
    assert resp.json()["code"] == "audio_too_large"


def test_sidecar_down_is_503_not_500(client, auth_headers, monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    app.dependency_overrides[voice_routes.get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(boom)
    )

    resp = client.post(
        "/api/voice/transcribe",
        files={"audio": ("c.webm", b"pretend", "audio/webm")},
        headers=auth_headers,
    )

    app.dependency_overrides.pop(voice_routes.get_http_client, None)

    assert resp.status_code == 503
    assert resp.json()["code"] == "stt_unavailable"


def test_capabilities_false_when_unconfigured(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "")

    resp = client.get("/api/voice/capabilities", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"stt": False}


def test_capabilities_true_when_configured(client, auth_headers, sidecar_ok):
    resp = client.get("/api/voice/capabilities", headers=auth_headers)

    assert resp.json() == {"stt": True}
