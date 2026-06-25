def test_404_uses_message_code_envelope(client, auth_headers):
    r = client.get("/api/sessions/does-not-exist", headers=auth_headers)
    assert r.status_code == 404
    body = r.json()
    assert body == {"message": "Session not found", "code": "not_found"}
    assert "detail" not in body


def test_401_envelope(client):
    r = client.get("/api/sessions")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_validation_error_envelope(client):
    # Missing required `password` → FastAPI body validation → 422, no auth gate.
    r = client.post("/api/auth/login", json={"email": "x@example.com"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "message" in body
    assert "detail" not in body


def test_timestamps_are_utc_z(client, auth_headers):
    sid = client.post("/api/sessions", headers=auth_headers).json()
    assert sid["createdAt"].endswith("Z")
