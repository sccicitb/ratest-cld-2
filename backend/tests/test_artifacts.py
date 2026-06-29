"""Stage A1 artifact backend tests.

Covers: create v1 → v2 (versioning), ownership gating (ToolError),
session_id from ctx (never args), artifact SSE event in stream,
raw endpoint (owner 200 + cross-user 404), listing endpoint,
session-delete cascade.
"""
from __future__ import annotations

import json

import pytest
from qdrant_client import QdrantClient

from app.chat.client import ModelChunk
from app.chat.routes import get_embedder_dep, get_qdrant
from app.main import app


# ---------------------------------------------------------------------------
# helpers / mocks
# ---------------------------------------------------------------------------


class FakeEmbedder:
    def embed_passages(self, texts):
        return [{"dense": [0.0] * 1024, "sparse": {"indices": [], "values": []}} for _ in texts]

    def embed_query(self, text):
        return {"dense": [0.0] * 1024, "sparse": {"indices": [], "values": []}}


class FakeModelClient:
    """Returns a single tool call to `create_artifact`, then a text response."""

    def __init__(self):
        self._call_count = 0

    async def stream(self, messages, tools):
        self._call_count += 1
        if self._call_count == 1:
            # First turn: emit tool call.
            yield ModelChunk(
                type="tool_call",
                id="tc_1",
                name="create_artifact",
                arguments={
                    "title": "Test Report",
                    "html": "<!DOCTYPE html><html><body><h1>Hello</h1></body></html>",
                },
            )
            yield ModelChunk(type="end")
        else:
            # Second turn: final answer (no tool calls).
            yield ModelChunk(type="text", text="Done.")
            yield ModelChunk(type="end")


class FakeModelClientUpdate:
    """Returns a tool call to `create_artifact` with an artifact_id to update."""

    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        self._call_count = 0

    async def stream(self, messages, tools):
        self._call_count += 1
        if self._call_count == 1:
            yield ModelChunk(
                type="tool_call",
                id="tc_1",
                name="create_artifact",
                arguments={
                    "artifact_id": self.artifact_id,
                    "title": "Updated Report",
                    "html": "<!DOCTYPE html><html><body><h1>Updated</h1></body></html>",
                },
            )
            yield ModelChunk(type="end")
        else:
            yield ModelChunk(type="text", text="Updated.")
            yield ModelChunk(type="end")


class FakeModelClientBogusArg:
    """Tries to pass session_id in args (should be ignored — ctx wins)."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._call_count = 0

    async def stream(self, messages, tools):
        self._call_count += 1
        if self._call_count == 1:
            yield ModelChunk(
                type="tool_call",
                id="tc_1",
                name="create_artifact",
                arguments={
                    "title": "Bogus Test",
                    "html": "<!DOCTYPE html><html></html>",
                    "session_id": self.session_id,
                },
            )
            yield ModelChunk(type="end")
        else:
            yield ModelChunk(type="text", text="OK")
            yield ModelChunk(type="end")


class FakeModelClientNonexistentUpdate:
    """Tries to update a non-existent artifact_id."""

    def __init__(self):
        self._call_count = 0

    async def stream(self, messages, tools):
        self._call_count += 1
        if self._call_count == 1:
            yield ModelChunk(
                type="tool_call",
                id="tc_1",
                name="create_artifact",
                arguments={
                    "artifact_id": "nonexistent-id",
                    "title": "Ghost",
                    "html": "<!DOCTYPE html><html></html>",
                },
            )
            yield ModelChunk(type="end")
        else:
            yield ModelChunk(type="text", text="Failed.")
            yield ModelChunk(type="end")


# ---------------------------------------------------------------------------
# SSE parser
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_deps():
    """Override Qdrant + embedder for all artifact tests."""
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


def test_list_requires_auth(client):
    r = client.get("/api/sessions/any/artifacts")
    assert r.status_code == 401


def test_raw_requires_auth(client):
    r = client.get("/api/sessions/any/artifacts/any/raw")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# v1 create + versioning + raw endpoint
# ---------------------------------------------------------------------------


def test_create_artifact_v1_via_chat(client, auth_headers):
    """Model creates v1 → raw endpoint serves the HTML."""
    from app.chat.routes import get_model_client

    sid = _create_session(client, auth_headers)
    fake_model = FakeModelClient()

    app.dependency_overrides[get_model_client] = lambda: fake_model

    r = client.post(
        f"/api/sessions/{sid}/chat",
        json={"message": "Make a report", "attachments": []},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)

    # Should contain an artifact event.
    artifact_events = [e for e in events if e["type"] == "artifact"]
    assert len(artifact_events) == 1
    ae = artifact_events[0]
    assert ae["artifactId"]
    assert ae["version"] == 1
    assert ae["title"] == "Test Report"

    # Should finish with done.
    assert any(e["type"] == "done" for e in events)

    artifact_id = ae["artifactId"]

    # Raw endpoint returns the HTML.
    r2 = client.get(
        f"/api/sessions/{sid}/artifacts/{artifact_id}/raw",
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.headers["content-type"].startswith("text/html")
    assert "Hello" in r2.text

    # Listing returns this artifact.
    r3 = client.get(
        f"/api/sessions/{sid}/artifacts",
        headers=auth_headers,
    )
    assert r3.status_code == 200
    items = r3.json()
    assert len(items) == 1
    assert items[0]["id"] == artifact_id
    assert items[0]["title"] == "Test Report"
    assert items[0]["latestVersion"] == 1

    app.dependency_overrides.pop(get_model_client, None)


def test_create_artifact_v2_update(client, auth_headers):
    """Update with artifact_id → v2, both versions reachable via ?version=."""
    from app.chat.routes import get_model_client

    sid = _create_session(client, auth_headers)

    # First, create v1.
    fake_v1 = FakeModelClient()
    app.dependency_overrides[get_model_client] = lambda: fake_v1
    r = client.post(
        f"/api/sessions/{sid}/chat",
        json={"message": "Make a report", "attachments": []},
        headers=auth_headers,
    )
    events = _parse_sse(r.text)
    artifact_id = [e for e in events if e["type"] == "artifact"][0]["artifactId"]

    # Now update to v2.
    fake_v2 = FakeModelClientUpdate(artifact_id)
    app.dependency_overrides[get_model_client] = lambda: fake_v2
    r2 = client.post(
        f"/api/sessions/{sid}/chat",
        json={"message": "Update it", "attachments": []},
        headers=auth_headers,
    )
    events2 = _parse_sse(r2.text)
    artifact_events2 = [e for e in events2 if e["type"] == "artifact"]
    assert len(artifact_events2) == 1
    assert artifact_events2[0]["version"] == 2

    # v1 is still reachable.
    r3 = client.get(
        f"/api/sessions/{sid}/artifacts/{artifact_id}/raw?version=1",
        headers=auth_headers,
    )
    assert r3.status_code == 200
    assert "Hello" in r3.text

    # v2 returns the updated content.
    r4 = client.get(
        f"/api/sessions/{sid}/artifacts/{artifact_id}/raw?version=2",
        headers=auth_headers,
    )
    assert r4.status_code == 200
    assert "Updated" in r4.text

    # latest_version is now 2.
    r5 = client.get(
        f"/api/sessions/{sid}/artifacts",
        headers=auth_headers,
    )
    items = r5.json()
    assert items[0]["latestVersion"] == 2

    app.dependency_overrides.pop(get_model_client, None)


# ---------------------------------------------------------------------------
# ownership — artifact_id from a different session
# ---------------------------------------------------------------------------


def test_cannot_update_other_sessions_artifact(client, auth_headers, session_factory):
    """ToolError when artifact_id belongs to a different session."""
    from app.chat.routes import get_model_client
    from app.models import Artifact

    sid_a = _create_session(client, auth_headers)
    sid_b = _create_session(client, auth_headers)

    # Create artifact in session A directly via the test DB.
    db = session_factory()
    artifact = Artifact(session_id=sid_a, title="Session A Report", latest_version=1)
    db.add(artifact)
    db.commit()
    artifact_id = artifact.id
    db.close()

    # Try to update via session B's chat — should get a ToolError.
    fake_model = FakeModelClientUpdate(artifact_id)
    app.dependency_overrides[get_model_client] = lambda: fake_model

    r = client.post(
        f"/api/sessions/{sid_b}/chat",
        json={"message": "Update it", "attachments": []},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    # The tool result should contain an error message (no artifact event, no update).
    artifact_events = [e for e in events if e["type"] == "artifact"]
    assert len(artifact_events) == 0

    # The artifact in session A should still be v1.
    db = session_factory()
    a = db.get(Artifact, artifact_id)
    assert a.latest_version == 1
    db.close()

    app.dependency_overrides.pop(get_model_client, None)


def test_raw_404_cross_user(client, auth_headers, session_factory):
    """Cross-user raw endpoint returns 404."""
    from app.auth.security import hash_password
    from app.models import Artifact, ChatSession, User

    # Create another user with their own session and artifact.
    db = session_factory()
    other = User(
        email="other2@example.com", display_name="Other2", password_hash=hash_password("x")
    )
    db.add(other)
    db.commit()
    other_session = ChatSession(user_id=other.id, title="New Chat")
    db.add(other_session)
    db.commit()
    artifact = Artifact(session_id=other_session.id, title="Other's", latest_version=1)
    db.add(artifact)
    db.commit()
    artifact_id = artifact.id
    sid = other_session.id
    db.close()

    # Demo user tries to access — 404.
    r = client.get(
        f"/api/sessions/{sid}/artifacts/{artifact_id}/raw",
        headers=auth_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# session_id from ctx, never from args
# ---------------------------------------------------------------------------


def test_session_id_from_ctx_not_args(client, auth_headers):
    """Model passes bogus session_id in args — ctx.session_id wins, tool works."""
    from app.chat.routes import get_model_client

    sid = _create_session(client, auth_headers)
    fake_model = FakeModelClientBogusArg("hacked-session-id")
    app.dependency_overrides[get_model_client] = lambda: fake_model

    r = client.post(
        f"/api/sessions/{sid}/chat",
        json={"message": "Bogus", "attachments": []},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    artifact_events = [e for e in events if e["type"] == "artifact"]
    assert len(artifact_events) == 1
    # The artifact should belong to the real session.
    artifact_id = artifact_events[0]["artifactId"]
    r2 = client.get(
        f"/api/sessions/{sid}/artifacts/{artifact_id}/raw",
        headers=auth_headers,
    )
    assert r2.status_code == 200

    app.dependency_overrides.pop(get_model_client, None)


# ---------------------------------------------------------------------------
# nonexistent artifact_id → ToolError
# ---------------------------------------------------------------------------


def test_create_artifact_nonexistent_id(client, auth_headers):
    """Updating a non-existent artifact_id returns a ToolError."""
    from app.chat.routes import get_model_client

    sid = _create_session(client, auth_headers)
    fake_model = FakeModelClientNonexistentUpdate()
    app.dependency_overrides[get_model_client] = lambda: fake_model

    r = client.post(
        f"/api/sessions/{sid}/chat",
        json={"message": "Update ghost", "attachments": []},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    # No artifact event emitted.
    artifact_events = [e for e in events if e["type"] == "artifact"]
    assert len(artifact_events) == 0

    app.dependency_overrides.pop(get_model_client, None)


# ---------------------------------------------------------------------------
# session delete cascades artifacts
# ---------------------------------------------------------------------------


def test_session_delete_cascades_artifacts(client, auth_headers):
    """After deleting a session, the artifact's raw endpoint returns 404."""
    from app.chat.routes import get_model_client

    sid = _create_session(client, auth_headers)
    fake_model = FakeModelClient()
    app.dependency_overrides[get_model_client] = lambda: fake_model

    r = client.post(
        f"/api/sessions/{sid}/chat",
        json={"message": "Make a report", "attachments": []},
        headers=auth_headers,
    )
    events = _parse_sse(r.text)
    artifact_id = [e for e in events if e["type"] == "artifact"][0]["artifactId"]

    # Verify raw works before delete.
    r2 = client.get(
        f"/api/sessions/{sid}/artifacts/{artifact_id}/raw",
        headers=auth_headers,
    )
    assert r2.status_code == 200

    # Delete the session.
    r3 = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r3.status_code == 204

    # Raw now returns 404 (session gone, artifact gone via cascade).
    r4 = client.get(
        f"/api/sessions/{sid}/artifacts/{artifact_id}/raw",
        headers=auth_headers,
    )
    assert r4.status_code == 404

    app.dependency_overrides.pop(get_model_client, None)


# ---------------------------------------------------------------------------
# listing without any artifacts
# ---------------------------------------------------------------------------


def test_list_artifacts_empty(client, auth_headers):
    """Listing for a new session returns empty list."""
    sid = _create_session(client, auth_headers)
    r = client.get(
        f"/api/sessions/{sid}/artifacts",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# artifact SSE event contains required fields
# ---------------------------------------------------------------------------


def test_artifact_sse_event_shape(client, auth_headers):
    """The artifact SSE event has type, artifactId, version, title in camelCase."""
    from app.chat.routes import get_model_client

    sid = _create_session(client, auth_headers)
    fake_model = FakeModelClient()
    app.dependency_overrides[get_model_client] = lambda: fake_model

    r = client.post(
        f"/api/sessions/{sid}/chat",
        json={"message": "Make a report", "attachments": []},
        headers=auth_headers,
    )
    events = _parse_sse(r.text)
    ae = [e for e in events if e["type"] == "artifact"][0]
    assert "artifactId" in ae
    assert "version" in ae
    assert "title" in ae
    assert isinstance(ae["version"], int)
    assert isinstance(ae["title"], str)

    app.dependency_overrides.pop(get_model_client, None)
