"""M3 (Pillar 2 v1.1) shared-KB security tests.

Covers the key security property: group-gated KB retrieval with no cross-group
leakage, public visibility, dedup, session privacy, and upload/list/delete
permission rules.

Fixtures use:
  - In-memory Qdrant + FakeEmbedder (fast, no model load)
  - In-memory SQLite DB (isolated per test via conftest session_factory)
  - Admin and regular-user auth-header fixtures from conftest
"""
from __future__ import annotations

import io
import json

import pytest
from qdrant_client import QdrantClient

from app.db import get_session_factory
from app.kb.routes import get_embedder_dep, get_qdrant
from app.main import app
from app.models import Group, KBFile, user_groups
from app.rag.retrieve import retrieve
from app.rag.vectors import ensure_collection, upsert_chunks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GROUP_A = "group-a-id"
_GROUP_B = "group-b-id"


class _FakeEmbedder:
    """Zero-vector embedder for fast test runs."""

    def embed_passages(self, texts):
        return [
            {"dense": [0.1] * 1024, "sparse": {"indices": [1, 2], "values": [1.0, 0.5]}}
            for _ in texts
        ]

    def embed_query(self, text):
        return {"dense": [0.1] * 1024, "sparse": {"indices": [1, 2], "values": [1.0, 0.5]}}


def _kb_chunk(file_id: str, content: str, group_id: str | None, is_public: bool = False):
    return {
        "content": content,
        "file_id": file_id,
        "chunk_idx": 0,
        "tags": [],
        "user_id": "sys",
        "scope": "kb",
        "session_id": None,
        "status": "ready",
        "group_id": group_id,
        "is_public": is_public,
    }


def _session_chunk(file_id: str, content: str, user_id: str, session_id: str):
    return {
        "content": content,
        "file_id": file_id,
        "chunk_idx": 0,
        "tags": [],
        "user_id": user_id,
        "scope": "session",
        "session_id": session_id,
        "status": "ready",
        "group_id": None,
        "is_public": False,
    }


def _parse_sse_events(raw: bytes) -> list[dict]:
    events = []
    for frame in raw.decode("utf-8").split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: "), f"unexpected frame: {frame!r}"
        events.append(json.loads(frame[len("data: "):]))
    return events


def _upload(client, headers, filename="notes.txt", content=b"Hello world. " * 200, **form):
    files = {"file": (filename, io.BytesIO(content), "text/plain")}
    return client.post("/api/knowledge-base/upload", headers=headers, files=files, data=form)


# ---------------------------------------------------------------------------
# In-memory Qdrant + FakeEmbedder shared across the file
# ---------------------------------------------------------------------------


@pytest.fixture()
def qdrant() -> QdrantClient:
    q = QdrantClient(":memory:")
    ensure_collection(q)
    return q


@pytest.fixture()
def fake_embedder() -> _FakeEmbedder:
    return _FakeEmbedder()


# ---------------------------------------------------------------------------
# Override KB route deps for API-level tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_kb_deps(qdrant, session_factory):
    app.dependency_overrides[get_qdrant] = lambda: qdrant
    app.dependency_overrides[get_embedder_dep] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    yield
    app.dependency_overrides.pop(get_qdrant, None)
    app.dependency_overrides.pop(get_embedder_dep, None)
    app.dependency_overrides.pop(get_session_factory, None)


# ---------------------------------------------------------------------------
# Group + user fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def group_a(session_factory) -> str:
    """Create group-a in the DB and return its id."""
    db = session_factory()
    g = Group(id=_GROUP_A, name="Group A", default_tags=["tag-a"])
    db.add(g)
    db.commit()
    db.close()
    return _GROUP_A


@pytest.fixture()
def group_b(session_factory) -> str:
    """Create group-b in the DB and return its id."""
    db = session_factory()
    g = Group(id=_GROUP_B, name="Group B", default_tags=["tag-b"])
    db.add(g)
    db.commit()
    db.close()
    return _GROUP_B


@pytest.fixture()
def user_a_headers(client, demo_user, session_factory, group_a):
    """demo_user added to group-a; returns auth headers."""
    db = session_factory()
    db.execute(user_groups.insert().values(user_id=demo_user["id"], group_id=group_a))
    db.commit()
    db.close()
    r = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


@pytest.fixture()
def user_b_headers(client, session_factory, group_b):
    """A second user in group-b; returns auth headers."""
    from app.auth.security import hash_password
    from app.models import User

    db = session_factory()
    u = User(email="user-b@example.com", display_name="User B", password_hash=hash_password("pass1234"))
    db.add(u)
    db.commit()
    db.refresh(u)
    uid = u.id
    db.execute(user_groups.insert().values(user_id=uid, group_id=group_b))
    db.commit()
    db.close()

    r = client.post("/api/auth/login", json={"email": "user-b@example.com", "password": "pass1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


@pytest.fixture()
def no_group_headers(client, session_factory):
    """A user with no group membership; returns auth headers."""
    from app.auth.security import hash_password
    from app.models import User

    db = session_factory()
    u = User(email="nogroup@example.com", display_name="No Group", password_hash=hash_password("pass1234"))
    db.add(u)
    db.commit()
    db.close()

    r = client.post("/api/auth/login", json={"email": "nogroup@example.com", "password": "pass1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


# ---------------------------------------------------------------------------
# §1 — Cross-group no-leak (the key security test)
# ---------------------------------------------------------------------------


def test_group_a_caller_cannot_see_group_b_chunks(qdrant, fake_embedder):
    """Caller in group-a retrieves A's content and NOT B's.

    Chunks are inserted directly via upsert_chunks to bypass the upload API,
    testing the Qdrant filter boundary in isolation.
    """
    upsert_chunks(qdrant, fake_embedder, [
        _kb_chunk("doc-a", "alpha secret document for group a only", _GROUP_A),
        _kb_chunk("doc-b", "beta secret document for group b only", _GROUP_B),
    ])

    results_a = retrieve(
        query="secret document",
        user_id="user-x",
        session_id=None,
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    file_ids_a = {r["file_id"] for r in results_a}
    assert "doc-a" in file_ids_a, "group-a caller must see group-a content"
    assert "doc-b" not in file_ids_a, "group-a caller must NOT see group-b content"


def test_group_b_caller_cannot_see_group_a_chunks(qdrant, fake_embedder):
    """Mirror of the above: group-b caller cannot see group-a docs."""
    upsert_chunks(qdrant, fake_embedder, [
        _kb_chunk("doc-a", "alpha secret document for group a only", _GROUP_A),
        _kb_chunk("doc-b", "beta secret document for group b only", _GROUP_B),
    ])

    results_b = retrieve(
        query="secret document",
        user_id="user-y",
        session_id=None,
        caller_group_ids=[_GROUP_B],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    file_ids_b = {r["file_id"] for r in results_b}
    assert "doc-b" in file_ids_b, "group-b caller must see group-b content"
    assert "doc-a" not in file_ids_b, "group-b caller must NOT see group-a content"


# ---------------------------------------------------------------------------
# §2 — Public docs visible to everyone, including no-group callers
# ---------------------------------------------------------------------------


def test_public_chunk_visible_to_any_caller(qdrant, fake_embedder):
    """A public KB chunk (is_public=True) is accessible regardless of group membership."""
    upsert_chunks(qdrant, fake_embedder, [
        _kb_chunk("public-doc", "public knowledge for all users", group_id=None, is_public=True),
        _kb_chunk("private-doc", "private knowledge for group a only", group_id=_GROUP_A, is_public=False),
    ])

    # No-group caller sees public, not private
    results_none = retrieve(
        query="knowledge",
        user_id="outsider",
        session_id=None,
        caller_group_ids=[],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    file_ids_none = {r["file_id"] for r in results_none}
    assert "public-doc" in file_ids_none
    assert "private-doc" not in file_ids_none

    # Group-a caller sees both public AND private
    results_a = retrieve(
        query="knowledge",
        user_id="member-a",
        session_id=None,
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    file_ids_a = {r["file_id"] for r in results_a}
    assert "public-doc" in file_ids_a
    assert "private-doc" in file_ids_a


# ---------------------------------------------------------------------------
# §3 — A+B caller sees both groups' docs
# ---------------------------------------------------------------------------


def test_member_of_both_groups_sees_all(qdrant, fake_embedder):
    """A user in both group-a and group-b retrieves docs from both."""
    upsert_chunks(qdrant, fake_embedder, [
        _kb_chunk("doc-a", "alpha content for group a", _GROUP_A),
        _kb_chunk("doc-b", "beta content for group b", _GROUP_B),
    ])

    results = retrieve(
        query="content",
        user_id="dual-member",
        session_id=None,
        caller_group_ids=[_GROUP_A, _GROUP_B],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    file_ids = {r["file_id"] for r in results}
    assert "doc-a" in file_ids
    assert "doc-b" in file_ids


# ---------------------------------------------------------------------------
# §4 — Session privacy: session chunk only visible in its own session
# ---------------------------------------------------------------------------


def test_session_chunk_invisible_to_different_session(qdrant, fake_embedder):
    """Session chunks do not leak to other sessions or to KB-only queries."""
    upsert_chunks(qdrant, fake_embedder, [
        _session_chunk("sess-doc", "private session note", user_id="user-1", session_id="sess-1"),
    ])

    # Correct session + user → visible
    results_ok = retrieve(
        query="private session note",
        user_id="user-1",
        session_id="sess-1",
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    assert any(r["file_id"] == "sess-doc" for r in results_ok)

    # Different session → invisible
    results_other = retrieve(
        query="private session note",
        user_id="user-1",
        session_id="sess-2",
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    assert not any(r["file_id"] == "sess-doc" for r in results_other)

    # KB-only query (session_id=None) → invisible
    results_kb = retrieve(
        query="private session note",
        user_id="user-1",
        session_id=None,
        caller_group_ids=[_GROUP_A],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    assert not any(r["file_id"] == "sess-doc" for r in results_kb)

    # Different user, same session_id → invisible (session filter requires user_id match)
    results_wrong_user = retrieve(
        query="private session note",
        user_id="user-2",
        session_id="sess-1",
        caller_group_ids=[],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    assert not any(r["file_id"] == "sess-doc" for r in results_wrong_user)


# ---------------------------------------------------------------------------
# §5 — Upload rules via the API
# ---------------------------------------------------------------------------


def test_upload_single_group_user_auto_files_into_own_group(
    client, user_a_headers, session_factory, demo_user, group_a
):
    """Regular user in one group: upload auto-files into that group, inherits default_tags."""
    r = _upload(client, user_a_headers)
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.content)
    assert events[-1]["type"] == "done"
    file_id = events[-2]["file"]["id"]

    # Check DB record
    db = session_factory()
    kf = db.get(KBFile, file_id)
    assert kf is not None
    assert kf.group_id == group_a
    assert kf.is_public is False
    assert "tag-a" in (kf.tags or [])  # inherits group default_tags
    db.close()


def test_upload_multi_group_user_must_pass_own_group_id(
    client, demo_user, session_factory, group_a, group_b
):
    """User in two groups must specify group_id; passing the wrong group → 403."""
    # Add demo user to both groups
    db = session_factory()
    db.execute(user_groups.insert().values(user_id=demo_user["id"], group_id=group_a))
    db.execute(user_groups.insert().values(user_id=demo_user["id"], group_id=group_b))
    db.commit()
    db.close()

    # Login fresh to get a token reflecting current membership
    r = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    headers = {"Authorization": f"Bearer {r.json()['accessToken']}"}

    # No group_id → 400 group_required
    r_no_group = _upload(client, headers)
    assert r_no_group.status_code == 400
    assert r_no_group.json()["code"] == "group_required"

    # Own group_id (group_a) → 200
    r_ok = _upload(client, headers, group_id=group_a)
    assert r_ok.status_code == 200, r_ok.text

    # A totally different group_id → 403 forbidden_group
    r_bad = _upload(client, headers, group_id="totally-unknown-group")
    assert r_bad.status_code == 403
    assert r_bad.json()["code"] == "forbidden_group"


def test_upload_no_group_user_gets_403_no_group(client, no_group_headers):
    """User with no group membership cannot upload (M3 rule)."""
    r = _upload(client, no_group_headers)
    assert r.status_code == 403
    assert r.json()["code"] == "no_group"


def test_admin_upload_any_group_public_and_tags(client, admin_headers, session_factory, group_a):
    """Admin may specify group_id, is_public, and tags freely."""
    r = _upload(
        client,
        admin_headers,
        group_id=group_a,
        is_public="true",   # Form field is string
        tags="finance,legal",
    )
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.content)
    file_id = events[-2]["file"]["id"]

    db = session_factory()
    kf = db.get(KBFile, file_id)
    assert kf.group_id == group_a
    assert kf.is_public is True
    assert "finance" in kf.tags
    assert "legal" in kf.tags
    db.close()


# ---------------------------------------------------------------------------
# §6 — List access
# ---------------------------------------------------------------------------


def test_list_regular_user_sees_only_own_group(
    client, user_a_headers, user_b_headers, group_a, group_b
):
    """User-a sees only group-a files; user-b sees only group-b files."""
    _upload(client, user_a_headers, filename="file-a.txt")
    _upload(client, user_b_headers, filename="file-b.txt")

    r_a = client.get("/api/knowledge-base", headers=user_a_headers)
    assert r_a.status_code == 200
    names_a = [f["name"] for f in r_a.json()]
    assert "file-a.txt" in names_a
    assert "file-b.txt" not in names_a

    r_b = client.get("/api/knowledge-base", headers=user_b_headers)
    assert r_b.status_code == 200
    names_b = [f["name"] for f in r_b.json()]
    assert "file-b.txt" in names_b
    assert "file-a.txt" not in names_b


def test_admin_list_sees_all_files(client, admin_headers, user_a_headers, user_b_headers):
    """Admin list is unrestricted — sees all KB files regardless of group."""
    _upload(client, user_a_headers, filename="file-a.txt")
    _upload(client, user_b_headers, filename="file-b.txt")

    r = client.get("/api/knowledge-base", headers=admin_headers)
    assert r.status_code == 200
    names = [f["name"] for f in r.json()]
    assert "file-a.txt" in names
    assert "file-b.txt" in names


# ---------------------------------------------------------------------------
# §7 — Delete / reindex permissions
# ---------------------------------------------------------------------------


def test_owner_can_delete_own_file(client, user_a_headers):
    """Uploader can delete their own file."""
    r = _upload(client, user_a_headers)
    events = _parse_sse_events(r.content)
    file_id = events[-2]["file"]["id"]

    r_del = client.delete(f"/api/knowledge-base/{file_id}", headers=user_a_headers)
    assert r_del.status_code == 204

    r_list = client.get("/api/knowledge-base", headers=user_a_headers)
    assert all(f["id"] != file_id for f in r_list.json())


def test_admin_can_delete_any_file(client, user_a_headers, admin_headers):
    """Admin can delete a file they did not upload."""
    r = _upload(client, user_a_headers)
    events = _parse_sse_events(r.content)
    file_id = events[-2]["file"]["id"]

    r_del = client.delete(f"/api/knowledge-base/{file_id}", headers=admin_headers)
    assert r_del.status_code == 204


def test_non_owner_non_admin_delete_returns_404(
    client, user_a_headers, user_b_headers
):
    """Non-owner, non-admin gets 404 trying to delete another user's file."""
    r = _upload(client, user_a_headers)
    events = _parse_sse_events(r.content)
    file_id = events[-2]["file"]["id"]

    r_del = client.delete(f"/api/knowledge-base/{file_id}", headers=user_b_headers)
    assert r_del.status_code == 404


def test_owner_can_reindex_own_file(client, user_a_headers):
    """Owner can trigger reindex."""
    r = _upload(client, user_a_headers)
    events = _parse_sse_events(r.content)
    file_id = events[-2]["file"]["id"]

    r_ri = client.post(f"/api/knowledge-base/{file_id}/reindex", headers=user_a_headers)
    assert r_ri.status_code == 200
    assert r_ri.json()["status"] == "indexing"


def test_non_owner_reindex_returns_404(client, user_a_headers, user_b_headers):
    """Non-owner, non-admin gets 404 on reindex."""
    r = _upload(client, user_a_headers)
    events = _parse_sse_events(r.content)
    file_id = events[-2]["file"]["id"]

    r_ri = client.post(f"/api/knowledge-base/{file_id}/reindex", headers=user_b_headers)
    assert r_ri.status_code == 404


# ---------------------------------------------------------------------------
# §8 — Dedup: same content in multiple accessible groups → returned ONCE
# ---------------------------------------------------------------------------


def test_retrieve_deduplicates_identical_content_across_groups(qdrant, fake_embedder):
    """If the same text exists in two groups a caller can access, retrieve returns it once."""
    shared_text = "This document appears in both group-a and group-b."
    upsert_chunks(qdrant, fake_embedder, [
        _kb_chunk("doc-a", shared_text, _GROUP_A),
        _kb_chunk("doc-b", shared_text, _GROUP_B),
    ])

    results = retrieve(
        query=shared_text,
        user_id="dual-member",
        session_id=None,
        caller_group_ids=[_GROUP_A, _GROUP_B],
        client=qdrant,
        embedder=fake_embedder,
        k=10,
    )
    contents = [r["content"] for r in results]
    assert contents.count(shared_text) == 1, (
        f"expected 1 occurrence after dedup, got {contents.count(shared_text)}"
    )
