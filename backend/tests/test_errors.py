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


def test_validation_error_envelope(client, auth_headers):
    # PATCH with a wrong-typed title triggers a 422 from FastAPI.
    r = client.patch("/api/sessions/x", headers=auth_headers, json={"title": 123})
    assert r.status_code in (404, 422)
    assert "message" in r.json() and "code" in r.json()


def test_timestamps_are_utc_z(client, auth_headers):
    sid = client.post("/api/sessions", headers=auth_headers).json()
    assert sid["createdAt"].endswith("Z")
