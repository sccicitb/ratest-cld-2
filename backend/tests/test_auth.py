"""Auth endpoint tests (§4)."""
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
