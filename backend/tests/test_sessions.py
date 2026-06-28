"""Sessions & messages endpoint tests (§5)."""

from __future__ import annotations
from unittest.mock import MagicMock, patch

from qdrant_client import QdrantClient

from app.chat.routes import get_qdrant
from app.main import app


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_sessions_require_auth(client):
    assert client.get("/api/sessions").status_code == 401


def test_create_list_get_rename_delete(client, auth_headers):
    # Stage 6: delete_session now needs a Qdrant client. Override with
    # an in-memory instance so the delete call doesn't hit localhost:6333.
    app.dependency_overrides[get_qdrant] = lambda: QdrantClient(location=":memory:")
    try:
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
        r = client.patch(f"/api/sessions/{sid}", headers=auth_headers, json={"title": "Renamed"})
        assert r.status_code == 200
        assert r.json()["title"] == "Renamed"

        # messages (empty)
        r = client.get(f"/api/sessions/{sid}/messages", headers=auth_headers)
        assert r.status_code == 200 and r.json() == []

        # delete
        assert client.delete(f"/api/sessions/{sid}", headers=auth_headers).status_code == 204
        assert client.get(f"/api/sessions/{sid}", headers=auth_headers).status_code == 404
    finally:
        app.dependency_overrides.pop(get_qdrant, None)


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
    other = User(email="other@example.com", display_name="Other", password_hash=hash_password("x"))
    db.add(other)
    db.flush()
    other_session = ChatSession(user_id=other.id, title="secret")
    db.add(other_session)
    db.commit()
    other_sid = other_session.id
    db.close()

    # 404 (not 403) — never leak existence.
    assert client.get(f"/api/sessions/{other_sid}", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------------------
# Stage 10: sandbox teardown on session delete
# ---------------------------------------------------------------------------


def _qdrant_in_memory():
    return QdrantClient(location=":memory:")


def _make_mock_httpx_client(delete_raises: Exception | None = None):
    """Return a fake httpx.Client context manager whose .delete() can raise."""
    mock_hc = MagicMock()
    if delete_raises:
        mock_hc.delete.side_effect = delete_raises
    else:
        mock_hc.delete.return_value = MagicMock(status_code=204)
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_hc)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return mock_ctx, mock_hc


def test_delete_session_issues_sandbox_delete(client, auth_headers):
    """Deleting a session triggers DELETE {code_exec_url}/sessions/{id}."""
    app.dependency_overrides[get_qdrant] = _qdrant_in_memory
    mock_ctx, mock_hc = _make_mock_httpx_client()
    try:
        r = client.post("/api/sessions", headers=auth_headers)
        assert r.status_code == 201
        sid = r.json()["id"]

        with patch("app.sessions.routes.httpx.Client", return_value=mock_ctx):
            resp = client.delete(f"/api/sessions/{sid}", headers=auth_headers)

        assert resp.status_code == 204
        # Verify the sandbox DELETE was called with the correct session_id in the URL
        mock_hc.delete.assert_called_once()
        called_url = mock_hc.delete.call_args[0][0]
        assert sid in called_url
    finally:
        app.dependency_overrides.pop(get_qdrant, None)


def test_delete_session_still_204_when_sandbox_call_fails(client, auth_headers):
    """Sandbox teardown failure is best-effort — deletion still returns 204."""
    app.dependency_overrides[get_qdrant] = _qdrant_in_memory
    mock_ctx, mock_hc = _make_mock_httpx_client(
        delete_raises=Exception("connection refused")
    )
    try:
        r = client.post("/api/sessions", headers=auth_headers)
        assert r.status_code == 201
        sid = r.json()["id"]

        with patch("app.sessions.routes.httpx.Client", return_value=mock_ctx):
            resp = client.delete(f"/api/sessions/{sid}", headers=auth_headers)

        assert resp.status_code == 204
    finally:
        app.dependency_overrides.pop(get_qdrant, None)
