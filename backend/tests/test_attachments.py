"""Stage 6 attachment SSE, inline context, session files, promote, delete tests.

Mirrors ``test_chat_route.py`` patterns: faked Qdrant (:memory:), faked
embedder, faked model client for inline-context capture.
"""

from __future__ import annotations

import json

import pytest
from qdrant_client import QdrantClient

from app.chat.client import ModelChunk, get_model_client
from app.chat.routes import get_embedder_dep, get_qdrant
from app.config import settings
from app.main import app
from app.rag.vectors import COLLECTION, ensure_collection


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeEmbedder:
    def embed_passages(self, texts):
        return [{"dense": [0.0] * 1024, "sparse": {"indices": [], "values": []}} for _ in texts]

    def embed_query(self, text):
        return {"dense": [0.0] * 1024, "sparse": {"indices": [], "values": []}}


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


def _create_session(client, auth_headers) -> str:
    r = client.post("/api/sessions", headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_deps():
    """Override Qdrant + embedder for all attachment tests — in-memory, fake."""
    qdrant = QdrantClient(location=":memory:")
    embedder = FakeEmbedder()
    app.dependency_overrides[get_qdrant] = lambda: qdrant
    app.dependency_overrides[get_embedder_dep] = lambda: embedder
    yield
    app.dependency_overrides.pop(get_qdrant, None)
    app.dependency_overrides.pop(get_embedder_dep, None)


# ---------------------------------------------------------------------------
# auth / ownership
# ---------------------------------------------------------------------------


def test_upload_requires_auth(client):
    r = client.post("/api/sessions/any/attachments")
    assert r.status_code == 401


def test_upload_404_for_nonexistent_session(client, auth_headers):
    r = client.post(
        "/api/sessions/nonexistent-session/attachments",
        files=[("files", ("test.txt", b"hello", "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_upload_404_for_other_users_session(client, auth_headers, session_factory):
    from app.auth.security import hash_password
    from app.models import ChatSession, User

    db = session_factory()
    other = User(email="other@example.com", display_name="Other", password_hash=hash_password("x"))
    db.add(other)
    db.commit()
    other_session = ChatSession(user_id=other.id, title="New Chat")
    db.add(other_session)
    db.commit()
    sid = other_session.id
    db.close()

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("test.txt", b"hello", "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# inline upload
# ---------------------------------------------------------------------------


def test_upload_inline_file(client, auth_headers):
    """Small text file → attachment_resolved with ingested=False, no Qdrant points."""
    sid = _create_session(client, auth_headers)

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("hello.txt", b"hello world", "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    assert events[-1]["type"] == "done"

    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    assert len(resolved) == 1
    att = resolved[0]["attachment"]
    assert att["ingested"] is False
    assert att["fileName"] == "hello.txt"
    assert "id" in att

    # No Qdrant points should have been created for an inline attachment.
    qdrant = app.dependency_overrides[get_qdrant]()
    ensure_collection(qdrant)
    points, _ = qdrant.scroll(COLLECTION, limit=10)
    assert len(points) == 0


def test_upload_multiple_inline_files(client, auth_headers):
    """Two small files → two attachment_resolved events, then done."""
    sid = _create_session(client, auth_headers)

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[
            ("files", ("a.txt", b"file A content", "text/plain")),
            ("files", ("b.txt", b"file B content", "text/plain")),
        ],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    events = _parse_sse(r.text)
    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    assert len(resolved) == 2
    names = {a["attachment"]["fileName"] for a in resolved}
    assert names == {"a.txt", "b.txt"}
    assert events[-1]["type"] == "done"


# ---------------------------------------------------------------------------
# ingest upload
# ---------------------------------------------------------------------------


def test_upload_ingest_file(client, auth_headers, session_factory, monkeypatch):
    """Large text file → chunk_progress + attachment_resolved with ingested=True."""
    monkeypatch.setattr(settings, "inline_token_budget", 5)
    sid = _create_session(client, auth_headers)

    # 50 words > 5 threshold → ingest
    large_content = b" ".join(b"word%d" % i for i in range(50))

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("large.txt", large_content, "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    events = _parse_sse(r.text)

    chunk_events = [e for e in events if e["type"] == "chunk_progress"]
    assert len(chunk_events) >= 1, "should have at least one chunk_progress event"

    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    assert len(resolved) == 1
    att = resolved[0]["attachment"]
    assert att["ingested"] is True
    assert att["fileName"] == "large.txt"

    assert events[-1]["type"] == "done"

    # Verify a session-scoped KBFile row was created (use session_factory
    # so we query the same in-memory DB the app is using).
    from app.models import KBFile

    db = session_factory()
    try:
        kbf = db.query(KBFile).filter(KBFile.session_id == sid, KBFile.scope == "session").first()
        assert kbf is not None
        assert kbf.status == "ready"
        assert kbf.chunk_count > 0
    finally:
        db.close()

    # Verify Qdrant points exist for this session.
    qdrant = app.dependency_overrides[get_qdrant]()
    ensure_collection(qdrant)
    points, _ = qdrant.scroll(COLLECTION, limit=50)
    session_points = [p for p in points if p.payload.get("session_id") == sid]
    assert len(session_points) > 0


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_upload_error_on_unsupported_type(client, auth_headers):
    """An unsupported file type should emit an error event but not crash the stream."""
    sid = _create_session(client, auth_headers)

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("movie.mkv", b"\x00\x01\x02", "video/x-matroska"))],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) >= 1


# ---------------------------------------------------------------------------
# inline attachment text in chat context
# ---------------------------------------------------------------------------


def test_chat_inlines_attachment_text(client, auth_headers):
    """Chat with an inline attachment → model receives text prepended to message."""
    sid = _create_session(client, auth_headers)

    # Upload a small inline file.
    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("note.txt", b"top secret project details", "text/plain"))],
        headers=auth_headers,
    )
    events = _parse_sse(r.text)
    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    att_id = resolved[0]["attachment"]["id"]

    # Capture what the model client receives.
    captured_messages: list[dict] = []

    class CaptureModelClient:
        def __init__(self, script):
            self._script = list(script)

        async def stream(self, messages, tools):
            captured_messages.extend(messages)
            chunks = self._script.pop(0) if self._script else []
            for chunk in chunks:
                yield chunk

    app.dependency_overrides[get_model_client] = lambda: CaptureModelClient(
        [[ModelChunk(type="text", text="I see your secret project.")]]
    )

    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "summarize this", "attachments": [{"id": att_id}]},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 200

    # The model should have received the inline attachment text.
    user_msgs = [m for m in captured_messages if m.get("role") == "user"]
    assert len(user_msgs) == 1
    content = user_msgs[0]["content"]
    assert "top secret project details" in content
    assert "[Attached file: note.txt]" in content
    assert "summarize this" in content


def test_chat_ingested_attachment_not_inlined(client, auth_headers, monkeypatch):
    """Ingested attachment should NOT be inlined in the model message."""
    monkeypatch.setattr(settings, "inline_token_budget", 5)
    sid = _create_session(client, auth_headers)

    # Upload a large (ingested) file.
    large_content = b" ".join(b"word%d" % i for i in range(50))
    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("big.txt", large_content, "text/plain"))],
        headers=auth_headers,
    )
    events = _parse_sse(r.text)
    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    att_id = resolved[0]["attachment"]["id"]
    assert resolved[0]["attachment"]["ingested"] is True

    captured_messages: list[dict] = []

    class CaptureModelClient:
        def __init__(self, script):
            self._script = list(script)

        async def stream(self, messages, tools):
            captured_messages.extend(messages)
            chunks = self._script.pop(0) if self._script else []
            for chunk in chunks:
                yield chunk

    app.dependency_overrides[get_model_client] = lambda: CaptureModelClient(
        [[ModelChunk(type="text", text="ok")]]
    )

    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "hi", "attachments": [{"id": att_id}]},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 200
    user_msgs = [m for m in captured_messages if m.get("role") == "user"]
    assert len(user_msgs) == 1
    # The ingested file's text should NOT be in the message.
    assert "word0" not in user_msgs[0]["content"]


# ---------------------------------------------------------------------------
# session files list + promote
# ---------------------------------------------------------------------------


def test_list_session_files(client, auth_headers, monkeypatch):
    """GET /sessions/{id}/files returns only session-scoped files."""
    monkeypatch.setattr(settings, "inline_token_budget", 5)
    sid = _create_session(client, auth_headers)

    # Upload one ingest file to create a session-scoped KBFile.
    large = b" ".join(b"w%d" % i for i in range(50))
    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("session_doc.txt", large, "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 200

    # List session files.
    r = client.get(f"/api/sessions/{sid}/files", headers=auth_headers)
    assert r.status_code == 200
    files = r.json()
    assert len(files) == 1
    assert files[0]["name"] == "session_doc.txt"
    assert files[0]["scope"] == "session"

    # It should NOT appear in the KB listing.
    r = client.get("/api/knowledge-base", headers=auth_headers)
    assert r.status_code == 200
    kb_files = r.json()
    kb_names = {f["name"] for f in kb_files}
    assert "session_doc.txt" not in kb_names


def test_promote_session_file(client, auth_headers, monkeypatch):
    """Promote flips scope→kb, file appears in KB listing and not in session files."""
    monkeypatch.setattr(settings, "inline_token_budget", 5)
    sid = _create_session(client, auth_headers)

    large = b" ".join(b"w%d" % i for i in range(50))
    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("promotable.txt", large, "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 200

    # Get the file ID.
    r = client.get(f"/api/sessions/{sid}/files", headers=auth_headers)
    files = r.json()
    assert len(files) == 1
    file_id = files[0]["id"]

    # Promote it.
    r = client.post(
        f"/api/sessions/{sid}/files/{file_id}/promote",
        headers=auth_headers,
    )
    assert r.status_code == 200
    promoted = r.json()
    assert promoted["scope"] == "kb"

    # Now it should NOT be in session files.
    r = client.get(f"/api/sessions/{sid}/files", headers=auth_headers)
    assert r.json() == []

    # But it SHOULD be in the KB listing.
    r = client.get("/api/knowledge-base", headers=auth_headers)
    kb_files = r.json()
    kb_names = {f["name"] for f in kb_files}
    assert "promotable.txt" in kb_names


def test_promote_404_for_non_session_file(client, auth_headers):
    """Promote on a file that isn't session-scoped should 404."""
    sid = _create_session(client, auth_headers)

    r = client.post(
        f"/api/sessions/{sid}/files/nonexistent-id/promote",
        headers=auth_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# delete session → Qdrant cleanup
# ---------------------------------------------------------------------------


def test_delete_session_purges_qdrant_points(client, auth_headers, monkeypatch):
    """Deleting a session removes its session-scoped Qdrant points."""
    monkeypatch.setattr(settings, "inline_token_budget", 5)
    sid = _create_session(client, auth_headers)

    # Upload an ingest file to create Qdrant points.
    large = b" ".join(b"w%d" % i for i in range(50))
    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("deletable.txt", large, "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 200

    # Confirm points exist before delete.
    qdrant = app.dependency_overrides[get_qdrant]()
    ensure_collection(qdrant)
    points_before, _ = qdrant.scroll(COLLECTION, limit=50)
    session_points_before = [p for p in points_before if p.payload.get("session_id") == sid]
    assert len(session_points_before) > 0, "session-scoped points should exist before delete"

    # Delete the session.
    r = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r.status_code == 204

    # Confirm points are gone after delete.
    points_after, _ = qdrant.scroll(COLLECTION, limit=50)
    session_points_after = [p for p in points_after if p.payload.get("session_id") == sid]
    assert len(session_points_after) == 0, "session-scoped points should be removed after delete"


def test_delete_session_404_for_other_user(client, auth_headers, session_factory):
    """Cannot delete another user's session."""
    from app.auth.security import hash_password
    from app.models import ChatSession, User

    db = session_factory()
    other = User(email="other2@example.com", display_name="O2", password_hash=hash_password("x"))
    db.add(other)
    db.commit()
    other_session = ChatSession(user_id=other.id, title="X")
    db.add(other_session)
    db.commit()
    sid = other_session.id
    db.close()

    r = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r.status_code == 404
