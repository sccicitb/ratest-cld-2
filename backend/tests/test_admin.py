"""Admin endpoint tests (§M1)."""
from __future__ import annotations

from app.auth.security import hash_password
from app.main import _bootstrap_admin
from app.models import User


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


def test_admin_guard_non_admin_403(client, auth_headers):
    r = client.get("/api/admin/users", headers=auth_headers)
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_admin_guard_no_token_401(client):
    assert client.get("/api/admin/users").status_code == 401


def test_admin_guard_admin_ok(client, admin_headers):
    r = client.get("/api/admin/users", headers=admin_headers)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Create user
# ---------------------------------------------------------------------------


def test_create_user_201(client, admin_headers):
    r = client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "displayName": "New User", "password": "pass1234"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "new@example.com"
    assert body["displayName"] == "New User"
    assert body["isAdmin"] is False
    assert body["disabled"] is False


def test_create_user_can_login(client, admin_headers):
    client.post(
        "/api/admin/users",
        json={"email": "logintest@example.com", "displayName": "Login Test", "password": "pass5678"},
        headers=admin_headers,
    )
    r = client.post(
        "/api/auth/login",
        json={"email": "logintest@example.com", "password": "pass5678"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["accessToken"]


def test_create_user_duplicate_email_409(client, admin_headers, demo_user):
    r = client.post(
        "/api/admin/users",
        json={"email": demo_user["email"], "displayName": "Dup", "password": "pass1234"},
        headers=admin_headers,
    )
    assert r.status_code == 409
    assert r.json()["code"] == "email_taken"


def test_create_user_with_is_admin(client, admin_headers):
    r = client.post(
        "/api/admin/users",
        json={"email": "admin2@example.com", "displayName": "Admin2", "password": "pass1234", "isAdmin": True},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["isAdmin"] is True


# ---------------------------------------------------------------------------
# List users
# ---------------------------------------------------------------------------


def test_list_users(client, admin_headers, demo_user):
    r = client.get("/api/admin/users", headers=admin_headers)
    assert r.status_code == 200, r.text
    emails = [u["email"] for u in r.json()]
    assert "admin@example.com" in emails
    assert demo_user["email"] in emails


# ---------------------------------------------------------------------------
# Disable / enable
# ---------------------------------------------------------------------------


def test_disable_blocks_login(client, admin_headers, demo_user):
    # Disable the user
    r = client.patch(
        f"/api/admin/users/{demo_user['id']}",
        json={"disabled": True},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["disabled"] is True

    # Login should now return 403 account_disabled
    r = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "account_disabled"


def test_disable_blocks_existing_token(client, admin_headers, demo_user, auth_headers):
    # auth_headers was issued before disable — token is still valid structurally
    # Disable the user
    client.patch(
        f"/api/admin/users/{demo_user['id']}",
        json={"disabled": True},
        headers=admin_headers,
    )
    # Old token must now be rejected
    r = client.get("/api/auth/me", headers=auth_headers)
    assert r.status_code == 403
    assert r.json()["code"] == "account_disabled"


def test_enable_restores_login(client, admin_headers, demo_user):
    # Disable first
    client.patch(
        f"/api/admin/users/{demo_user['id']}",
        json={"disabled": True},
        headers=admin_headers,
    )
    # Re-enable
    r = client.patch(
        f"/api/admin/users/{demo_user['id']}",
        json={"disabled": False},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["disabled"] is False

    # Login works again
    r = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Self-lockout guard
# ---------------------------------------------------------------------------


def test_admin_cannot_disable_self(client, admin_headers, admin_user):
    r = client.patch(
        f"/api/admin/users/{admin_user['id']}",
        json={"disabled": True},
        headers=admin_headers,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "self_lockout"


def test_admin_cannot_demote_self(client, admin_headers, admin_user):
    r = client.patch(
        f"/api/admin/users/{admin_user['id']}",
        json={"isAdmin": False},
        headers=admin_headers,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "self_lockout"


def test_admin_can_patch_own_display_name(client, admin_headers, admin_user):
    r = client.patch(
        f"/api/admin/users/{admin_user['id']}",
        json={"displayName": "New Admin Name"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["displayName"] == "New Admin Name"


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------


def test_reset_password_returns_temp_and_works(client, admin_headers, demo_user):
    r = client.post(
        f"/api/admin/users/{demo_user['id']}/reset-password",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    temp_pw = r.json()["tempPassword"]
    assert temp_pw  # non-empty

    # Old password no longer works
    r_old = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    assert r_old.status_code == 401

    # Temp password logs in
    r_new = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": temp_pw},
    )
    assert r_new.status_code == 200, r_new.text


def test_reset_password_404(client, admin_headers):
    r = client.post(
        "/api/admin/users/nonexistent-id/reset-password",
        headers=admin_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Admin bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_creates_admin(session_factory):
    """_bootstrap_admin creates an admin when admin_email/password are set."""
    from app.config import settings

    db = session_factory()
    settings.admin_email = "bootstrap@example.com"
    settings.admin_password = "bootstrappass"
    try:
        # Patch SessionLocal to use our test session_factory
        from app import db as db_mod
        original_sl = db_mod.SessionLocal
        db_mod.SessionLocal = session_factory  # type: ignore[assignment]

        _bootstrap_admin()

        user = db.query(User).filter(User.email == "bootstrap@example.com").first()
        assert user is not None
        assert user.is_admin is True

        # Idempotent: calling again doesn't error or duplicate
        _bootstrap_admin()
        count = db.query(User).filter(User.email == "bootstrap@example.com").count()
        assert count == 1
    finally:
        settings.admin_email = None
        settings.admin_password = None
        db_mod.SessionLocal = original_sl  # type: ignore[assignment]
        db.close()


def test_bootstrap_promotes_existing_user(session_factory):
    """_bootstrap_admin promotes an existing non-admin user."""
    from app.config import settings
    from app import db as db_mod

    db = session_factory()
    # Create a regular user with that email
    user = User(
        email="tobe-promoted@example.com",
        display_name="Regular",
        password_hash=hash_password("pass"),
        is_admin=False,
    )
    db.add(user)
    db.commit()

    settings.admin_email = "tobe-promoted@example.com"
    settings.admin_password = "anypass"
    original_sl = db_mod.SessionLocal
    db_mod.SessionLocal = session_factory  # type: ignore[assignment]
    try:
        _bootstrap_admin()
        db.refresh(user)
        assert user.is_admin is True
    finally:
        settings.admin_email = None
        settings.admin_password = None
        db_mod.SessionLocal = original_sl  # type: ignore[assignment]
        db.close()
