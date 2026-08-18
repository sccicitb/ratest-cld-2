"""Backend voice proxy: auth, limits, and honest failure when the sidecar is down."""
from __future__ import annotations

import asyncio

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
    assert resp.json() == {"stt": False, "tts": False}


def test_capabilities_reports_the_startup_probe_not_just_the_url(
    client, auth_headers, monkeypatch
):
    """Spec §5: configured AND /health answered.

    A URL in .env with nothing behind it is exactly the case that produced a mic
    button for every user and a 503 on every press.
    """
    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    monkeypatch.setattr(app.state, voice_routes.STT_READY_ATTR, False,
                        raising=False)

    assert client.get("/api/voice/capabilities", headers=auth_headers).json() == {
        "stt": False,
        "tts": False,
    }

    monkeypatch.setattr(app.state, voice_routes.STT_READY_ATTR, True, raising=False)

    assert client.get("/api/voice/capabilities", headers=auth_headers).json() == {
        "stt": True,
        "tts": False,
    }


def _stub_probe(monkeypatch, response: httpx.Response | Exception) -> dict:
    """Point `probe_sidecar`'s client at a canned /health response."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(
        voice_routes, "_probe_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return seen


def test_probe_is_false_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "")

    assert asyncio.run(voice_routes.probe_sidecar()) == (False, False)


def test_probe_is_false_when_the_sidecar_is_not_running(monkeypatch):
    """The probe must swallow connection errors: a voice sidecar that is down
    cannot be allowed to stop the backend from starting."""
    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    _stub_probe(monkeypatch, httpx.ConnectError("refused"))

    assert asyncio.run(voice_routes.probe_sidecar()) == (False, False)


def test_probe_is_false_on_a_non_200_health(monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    _stub_probe(monkeypatch, httpx.Response(503))

    assert asyncio.run(voice_routes.probe_sidecar()) == (False, False)


def test_probe_is_true_when_health_answers(monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    seen = _stub_probe(monkeypatch, httpx.Response(200, json={"status": "ok"}))

    # A sidecar with STT but no TTS block reports (True, False): the read
    # button stays hidden rather than appearing and 502-ing.
    assert asyncio.run(voice_routes.probe_sidecar()) == (True, False)
    assert seen["url"] == "http://voice:8002/health"


def test_probe_reports_tts_when_health_advertises_it(monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    _stub_probe(monkeypatch, httpx.Response(
        200, json={"status": "ok", "tts": {"engine": "supertonic", "voices": ["F2"]}}
    ))

    assert asyncio.run(voice_routes.probe_sidecar()) == (True, True)


# --- TTS proxy (§1b) ---------------------------------------------------------


@pytest.fixture()
def tts_sidecar(monkeypatch):
    """Stub the sidecar's /synthesize, recording the forwarded JSON body."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.update(_json.loads(request.content))
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"RIFFfake",
                              headers={"content-type": "audio/wav"})

    monkeypatch.setattr(settings, "voice_service_url", "http://voice:8002")
    app.dependency_overrides[voice_routes.get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    yield seen
    app.dependency_overrides.pop(voice_routes.get_http_client, None)


def test_speak_requires_auth(client):
    assert client.post("/api/voice/speak", json={"text": "halo"}).status_code == 401


def test_speak_forwards_the_users_stored_voice(client, auth_headers, tts_sidecar):
    """The voice comes from the authenticated user, never from the request
    body -- the same rule as ToolContext scope in §7."""
    client.patch("/api/auth/me", json={"voice": "M3"}, headers=auth_headers)

    resp = client.post(
        "/api/voice/speak",
        json={"text": "Dokumen ditemukan.", "voice": "M5"},  # ignored on purpose
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"RIFFfake"
    assert tts_sidecar["voice"] == "M3"
    assert tts_sidecar["text"] == "Dokumen ditemukan."
    assert tts_sidecar["url"] == "http://voice:8002/synthesize"


def test_speak_is_503_when_voice_is_not_configured(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "voice_service_url", "")
    resp = client.post("/api/voice/speak", json={"text": "halo"}, headers=auth_headers)
    assert resp.status_code == 503
    assert resp.json()["code"] == "tts_unavailable"


def test_speak_rejects_text_over_the_backend_cap(client, auth_headers, tts_sidecar):
    resp = client.post(
        "/api/voice/speak",
        json={"text": "a" * (settings.max_tts_chars + 1)},
        headers=auth_headers,
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "text_too_long"


def test_capabilities_reports_tts_from_the_probe(client, auth_headers):
    client.app.state.voice_stt_ready = True
    client.app.state.voice_tts_ready = True
    body = client.get("/api/voice/capabilities", headers=auth_headers).json()
    assert body == {"stt": True, "tts": True}
