"""Sessions & messages endpoint tests (§5)."""
from __future__ import annotations


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_sessions_require_auth(client):
    assert client.get("/api/sessions").status_code == 401


def test_create_list_get_rename_delete(client, auth_headers):
    # create
    r = client.post("/api/sessions", headers=auth_headers)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["title"] == "New Chat"

    # list
    r = client.get("/api/sessions", headers=auth_headers)
    assert r.status_code == 200
    assert [s["id"] for s in r.json()] == [sid]

    # get
    assert client.get(f"/api/sessions/{sid}", headers=auth_headers).status_code == 200

    # rename
    r = client.patch(
        f"/api/sessions/{sid}", headers=auth_headers, json={"title": "Renamed"}
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"

    # messages (empty)
    r = client.get(f"/api/sessions/{sid}/messages", headers=auth_headers)
    assert r.status_code == 200 and r.json() == []

    # delete
    assert client.delete(f"/api/sessions/{sid}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/sessions/{sid}", headers=auth_headers).status_code == 404


def test_list_sorted_by_updated_desc(client, auth_headers):
    a = client.post("/api/sessions", headers=auth_headers).json()["id"]
    b = client.post("/api/sessions", headers=auth_headers).json()["id"]
    # Touch `a` so it becomes most-recently-updated.
    client.patch(f"/api/sessions/{a}", headers=auth_headers, json={"title": "bump"})
    ids = [s["id"] for s in client.get("/api/sessions", headers=auth_headers).json()]
    assert ids == [a, b]


def test_cannot_access_another_users_session(client, auth_headers, session_factory):
    from app.auth.security import hash_password
    from app.models import ChatSession, User

    db = session_factory()
    other = User(
        email="other@example.com", display_name="Other", password_hash=hash_password("x")
    )
    db.add(other)
    db.flush()
    other_session = ChatSession(user_id=other.id, title="secret")
    db.add(other_session)
    db.commit()
    other_sid = other_session.id
    db.close()

    # 404 (not 403) — never leak existence.
    assert client.get(f"/api/sessions/{other_sid}", headers=auth_headers).status_code == 404
