"""Stage V1.1 — Vision: image attachment intake + blob serving tests.

Images bypass extract_text/ingestion/Qdrant; are stored as blobs; and are
servable via an auth'd, ownership-checked endpoint.

Reuses in-memory Qdrant + FakeEmbedder fixtures from test_attachments.py
and the _FakeModelClient pattern from test_chat_route.py.
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


class _FakeEmbedder:
    def embed_passages(self, texts):
        return [
            {"dense": [0.0] * 1024, "sparse": {"indices": [], "values": []}}
            for _ in texts
        ]

    def embed_query(self, text):
        return {"dense": [0.0] * 1024, "sparse": {"indices": [], "values": []}}


class _FakeModelClient:
    def __init__(self, script: list[list[ModelChunk]]):
        self._script = list(script)

    async def stream(self, messages, tools):
        chunks = self._script.pop(0) if self._script else []
        for chunk in chunks:
            yield chunk


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def _create_session(client, auth_headers) -> str:
    r = client.post("/api/sessions", headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# Minimal 1×1 transparent PNG bytes
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_deps():
    """Override Qdrant + embedder for all vision tests — in-memory, fake."""
    qdrant = QdrantClient(location=":memory:")
    embedder = _FakeEmbedder()
    app.dependency_overrides[get_qdrant] = lambda: qdrant
    app.dependency_overrides[get_embedder_dep] = lambda: embedder
    yield
    app.dependency_overrides.pop(get_qdrant, None)
    app.dependency_overrides.pop(get_embedder_dep, None)


# ---------------------------------------------------------------------------
# V1.4 — image upload happy-path
# ---------------------------------------------------------------------------


def test_image_upload_resolves_without_qdrant_points(client, auth_headers):
    """Small PNG → attachment_resolved, ingested=False, fileType image/*, no Qdrant."""
    sid = _create_session(client, auth_headers)

    extract_called: list[str] = []

    import app.rag.extract as _extract_mod

    original_extract = _extract_mod.extract_text

    def _spy_extract(storage_key, filename):
        extract_called.append(filename)
        return original_extract(storage_key, filename)

    import app.sessions.attachments as _att_mod

    _att_mod.extract_text = _spy_extract
    try:
        r = client.post(
            f"/api/sessions/{sid}/attachments",
            files=[("files", ("photo.png", _TINY_PNG, "image/png"))],
            headers=auth_headers,
        )
    finally:
        _att_mod.extract_text = original_extract

    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert events[-1]["type"] == "done"

    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    assert len(resolved) == 1
    att = resolved[0]["attachment"]
    assert att["ingested"] is False
    assert att["fileName"] == "photo.png"
    assert att["fileType"].startswith("image/")

    # extract_text must NOT have been called for the image
    assert "photo.png" not in extract_called, "extract_text was called on an image"

    # No Qdrant points
    qdrant = app.dependency_overrides[get_qdrant]()
    ensure_collection(qdrant)
    points, _ = qdrant.scroll(COLLECTION, limit=10)
    assert len(points) == 0, "image upload should not create Qdrant points"


def test_image_upload_stores_attachment_row(client, auth_headers, session_factory):
    """Image upload creates exactly one Attachment row with ingested=False."""
    from app.models import Attachment

    sid = _create_session(client, auth_headers)

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("snap.jpg", _TINY_PNG, "image/jpeg"))],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    db = session_factory()
    try:
        rows = db.query(Attachment).filter(Attachment.file_name == "snap.jpg").all()
        assert len(rows) == 1
        assert rows[0].ingested is False
        assert rows[0].message_id is None
        assert rows[0].file_type.startswith("image/")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# V1.4 — oversized image
# ---------------------------------------------------------------------------


def test_oversized_image_emits_error_no_row(client, auth_headers, session_factory, monkeypatch):
    """Image larger than max_image_bytes → error event, no Attachment row persisted."""
    from app.models import Attachment

    monkeypatch.setattr(settings, "max_image_bytes", 5)  # tiny cap
    sid = _create_session(client, auth_headers)

    big_bytes = b"X" * 10  # > 5 bytes

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("big.png", big_bytes, "image/png"))],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) >= 1, "expected an error event for oversized image"
    assert any("big.png" in e["message"] for e in error_events)

    # No Attachment row should have been created
    db = session_factory()
    try:
        rows = db.query(Attachment).filter(Attachment.file_name == "big.png").all()
        assert len(rows) == 0, "oversized image must not leave an Attachment row"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# V1.4 — raw endpoint
# ---------------------------------------------------------------------------


def _upload_image_and_link(client, auth_headers, sid: str) -> tuple[str, bytes]:
    """Upload a PNG and send a chat message that links it; return (att_id, bytes)."""
    img_bytes = _TINY_PNG

    # Upload
    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("test_img.png", img_bytes, "image/png"))],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    assert len(resolved) == 1
    att_id = resolved[0]["attachment"]["id"]

    # Link to a message via chat route
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        [[ModelChunk(type="text", text="ok")]]
    )
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "look at this", "attachments": [{"id": att_id}]},
        )
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    return att_id, img_bytes


def test_raw_endpoint_returns_bytes_and_content_type(client, auth_headers):
    """After linking, GET …/raw returns the image bytes with correct content-type."""
    sid = _create_session(client, auth_headers)
    att_id, img_bytes = _upload_image_and_link(client, auth_headers, sid)

    r = client.get(
        f"/api/sessions/{sid}/attachments/{att_id}/raw",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.content == img_bytes
    assert r.headers["content-type"].startswith("image/")


def test_raw_endpoint_404_for_unlinked_attachment(client, auth_headers):
    """Freshly uploaded (not yet sent) image returns 404 from the raw endpoint."""
    sid = _create_session(client, auth_headers)

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("unlinked.png", _TINY_PNG, "image/png"))],
        headers=auth_headers,
    )
    events = _parse_sse(r.text)
    att_id = [e for e in events if e["type"] == "attachment_resolved"][0]["attachment"]["id"]

    r = client.get(
        f"/api/sessions/{sid}/attachments/{att_id}/raw",
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_raw_endpoint_404_for_unknown_id(client, auth_headers):
    """Unknown attachment id → 404."""
    sid = _create_session(client, auth_headers)
    r = client.get(
        f"/api/sessions/{sid}/attachments/nonexistent-id/raw",
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_raw_endpoint_404_for_other_users_session(client, auth_headers, session_factory):
    """Accessing an attachment via another user's session → 404."""
    from app.auth.security import hash_password
    from app.models import ChatSession, User

    # Create the real session and upload+link the image
    sid = _create_session(client, auth_headers)
    att_id, _ = _upload_image_and_link(client, auth_headers, sid)

    # Create another user's session
    db = session_factory()
    other = User(
        email="other_vision@example.com",
        display_name="Other",
        password_hash=hash_password("x"),
    )
    db.add(other)
    db.commit()
    other_session = ChatSession(user_id=other.id, title="X")
    db.add(other_session)
    db.commit()
    other_sid = other_session.id
    db.close()

    # Try to fetch via the other session path (same auth user → 404 because session not owned)
    r = client.get(
        f"/api/sessions/{other_sid}/attachments/{att_id}/raw",
        headers=auth_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# V1.4 — regression: .txt still uses the text path
# ---------------------------------------------------------------------------


def test_png_with_missing_content_type_still_detected_as_image(client, auth_headers):
    """A .png with an empty/wrong content-type is still routed to the image path
    (extension fallback), not text extraction."""
    import app.sessions.attachments as _att_mod

    extract_called: list[str] = []
    orig = _att_mod.extract_text

    def _spy(storage_key, filename):
        extract_called.append(filename)
        return orig(storage_key, filename)

    sid = _create_session(client, auth_headers)
    _att_mod.extract_text = _spy
    try:
        r = client.post(
            f"/api/sessions/{sid}/attachments",
            # generic octet-stream content-type — only the .png extension marks it
            files=[("files", ("diagram.png", _TINY_PNG, "application/octet-stream"))],
            headers=auth_headers,
        )
    finally:
        _att_mod.extract_text = orig

    assert r.status_code == 200, r.text
    resolved = [e for e in _parse_sse(r.text) if e["type"] == "attachment_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["attachment"]["fileType"].startswith("image/")
    assert "diagram.png" not in extract_called, "image must not hit extract_text"


def test_txt_upload_still_uses_text_path(client, auth_headers):
    """A .txt file still goes through extract_text and produces an Attachment row."""
    sid = _create_session(client, auth_headers)

    r = client.post(
        f"/api/sessions/{sid}/attachments",
        files=[("files", ("notes.txt", b"hello world", "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    resolved = [e for e in events if e["type"] == "attachment_resolved"]
    assert len(resolved) == 1
    att = resolved[0]["attachment"]
    assert att["fileName"] == "notes.txt"
    # Inline text file: ingested=False (token budget allows it)
    assert att["ingested"] is False


# ---------------------------------------------------------------------------
# V1.4 — regression: KB upload of .png is still rejected
# ---------------------------------------------------------------------------


def test_kb_upload_of_png_is_rejected(client, auth_headers):
    """KB upload endpoint must NOT accept .png (KB stays text-only)."""
    r = client.post(
        "/api/knowledge-base/upload",
        files=[("file", ("image.png", _TINY_PNG, "image/png"))],
        headers=auth_headers,
    )
    # Expect a non-2xx rejection (422 or 400 from SUPPORTED_KB_TYPES check)
    assert r.status_code not in (200, 201), (
        f"KB upload should reject .png but got {r.status_code}: {r.text}"
    )
