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


@pytest.fixture()
def sidecar_says(monkeypatch):
    """Let a test choose the sidecar's exact status + body."""
    def _install(status: int, body):
        def handler(request: httpx.Request) -> httpx.Response:
            if isinstance(body, (bytes, str)):
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=body)

        monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
        app.dependency_overrides[voice_routes.get_http_client] = (
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    yield _install
    app.dependency_overrides.pop(voice_routes.get_http_client, None)


def _post_clip(client, auth_headers):
    return client.post(
        "/api/voice/transcribe",
        files={"audio": ("c.webm", b"pretend", "audio/webm")},
        headers=auth_headers,
    )


def test_audio_too_long_reaches_the_user_as_itself(client, auth_headers, sidecar_says):
    """Flattening this to stt_failed/502 loses the only actionable part: the
    user recorded too much and can fix it by recording less."""
    sidecar_says(413, {"message": "Recording is 300s; the limit is 120s",
                       "code": "audio_too_long"})

    resp = _post_clip(client, auth_headers)

    assert resp.status_code == 413
    assert resp.json()["code"] == "audio_too_long"
    assert "120s" in resp.json()["message"]


def test_undecodable_audio_passes_through_as_400(client, auth_headers, sidecar_says):
    sidecar_says(400, {"message": "undecodable audio: bad header",
                       "code": "audio_undecodable"})

    resp = _post_clip(client, auth_headers)

    assert resp.status_code == 400
    assert resp.json()["code"] == "audio_undecodable"


def test_sidecar_5xx_is_still_stt_failed(client, auth_headers, sidecar_says):
    """A sidecar crash is our problem, not the user's -- no pass-through."""
    sidecar_says(500, {"detail": "boom"})

    resp = _post_clip(client, auth_headers)

    assert resp.status_code == 502
    assert resp.json()["code"] == "stt_failed"


def test_unknown_sidecar_4xx_code_does_not_leak(client, auth_headers, sidecar_says):
    """The client's `code` vocabulary stays a closed set: an unrecognised code
    from a future sidecar version becomes stt_failed rather than reaching the UI
    as something it has no branch for."""
    sidecar_says(422, {"message": "whatever", "code": "some_future_code"})

    resp = _post_clip(client, auth_headers)

    assert resp.status_code == 502
    assert resp.json()["code"] == "stt_failed"


def test_non_json_sidecar_4xx_does_not_500(client, auth_headers, sidecar_says):
    sidecar_says(404, b"<html>nginx</html>")

    resp = _post_clip(client, auth_headers)

    assert resp.status_code == 502
    assert resp.json()["code"] == "stt_failed"


def test_capabilities_false_when_unconfigured(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "")

    resp = client.get("/api/voice/capabilities", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"stt": False}


def test_capabilities_true_when_configured(client, auth_headers, sidecar_ok):
    resp = client.get("/api/voice/capabilities", headers=auth_headers)

    assert resp.json() == {"stt": True}
