"""Auth endpoint tests (§4) + M1 additions (change-password, disabled lockout)."""
from __future__ import annotations


def test_login_bad_credentials(client, demo_user):
    r = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": "wrong"},
    )
    assert r.status_code == 401


def test_login_success_returns_token_and_user(client, demo_user):
    r = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accessToken"]
    assert body["user"]["email"] == demo_user["email"]
    assert body["user"]["displayName"] == "Alex Demo"  # camelCase on the wire
    assert "refresh_token" in r.cookies


def test_me_requires_bearer(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_user(client, auth_headers, demo_user):
    r = client.get("/api/auth/me", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == demo_user["email"]


def test_refresh_rotates_and_returns_auth(client, demo_user):
    login = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    old_cookie = login.cookies["refresh_token"]

    r = client.post("/api/auth/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["accessToken"]
    assert r.json()["user"]["email"] == demo_user["email"]
    # Rotation: a fresh refresh cookie is issued.
    assert r.cookies["refresh_token"] != old_cookie


def test_refresh_without_cookie_401(client):
    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_revokes_refresh(client, demo_user):
    client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    assert client.post("/api/auth/logout").status_code == 200
    # Cookie cleared → refresh no longer works.
    client.cookies.clear()
    assert client.post("/api/auth/refresh").status_code == 401


# ---------------------------------------------------------------------------
# change-password (§M1)
# ---------------------------------------------------------------------------


def test_change_password_wrong_old_400(client, auth_headers):
    r = client.post(
        "/api/auth/change-password",
        json={"oldPassword": "wrongpass", "newPassword": "newpass1234"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_password"


def test_change_password_too_short_400(client, auth_headers):
    r = client.post(
        "/api/auth/change-password",
        json={"oldPassword": "demo1234", "newPassword": "short"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "password_too_short"


def test_change_password_success(client, demo_user, auth_headers):
    r = client.post(
        "/api/auth/change-password",
        json={"oldPassword": "demo1234", "newPassword": "newpassword1"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    # Old password no longer works
    r_old = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": "demo1234"},
    )
    assert r_old.status_code == 401

    # New password works
    r_new = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": "newpassword1"},
    )
    assert r_new.status_code == 200, r_new.text


def test_change_password_requires_auth(client):
    r = client.post(
        "/api/auth/change-password",
        json={"oldPassword": "demo1234", "newPassword": "newpassword1"},
    )
    assert r.status_code == 401


# --- Voice preference (§1b) --------------------------------------------------


def test_new_user_defaults_to_f2(client, auth_headers):
    body = client.get("/api/auth/me", headers=auth_headers).json()
    assert body["voice"] == "F2"


def test_patch_me_updates_the_voice(client, auth_headers):
    resp = client.patch("/api/auth/me", json={"voice": "M2"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["voice"] == "M2"
    assert client.get("/api/auth/me", headers=auth_headers).json()["voice"] == "M2"


def test_patch_me_rejects_an_unknown_voice(client, auth_headers):
    """The value reaches a filesystem path inside the TTS engine, so an
    invalid one must never be persisted."""
    resp = client.patch(
        "/api/auth/me", json={"voice": "../../etc/passwd"}, headers=auth_headers
    )
    assert resp.status_code == 422
    assert client.get("/api/auth/me", headers=auth_headers).json()["voice"] == "F2"


def test_patch_me_requires_authentication(client):
    assert client.patch("/api/auth/me", json={"voice": "M2"}).status_code == 401
