"""KB endpoint tests (Tasks 3.5-3.7): list/filter, upload (SSE), reindex, tags, delete.

Routes use a fake embedder + an in-memory Qdrant client (overridden via
get_qdrant/get_embedder_dep) so these stay fast — no BGE-M3 model load.

M3 (Pillar 2 v1.1): uploads require the caller to be in a group (or be admin).
The ``_demo_group`` autouse fixture creates a Group and adds the demo user to it
so all upload tests work. Tests that explicitly verify the no-group-403 path use
a fresh user with no group membership.
"""
from __future__ import annotations

import io
import json
import time

import pytest
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.db import get_session_factory
from app.kb.routes import get_embedder_dep, get_ingest_jobs, get_qdrant
from app.main import app
from app.models import Group, user_groups
from app.rag.ingest_jobs import IngestJobRegistry
from app.rag.vectors import COLLECTION, ensure_collection


class _FakeEmbedder:
    def embed_passages(self, texts):
        return [
            {"dense": [0.1] * 1024, "sparse": {"indices": [1, 2], "values": [1.0, 0.5]}}
            for _ in texts
        ]

    def embed_query(self, text):
        return {"dense": [0.1] * 1024, "sparse": {"indices": [1, 2], "values": [1.0, 0.5]}}


@pytest.fixture()
def qdrant_memory():
    client = QdrantClient(":memory:")
    ensure_collection(client)
    return client


@pytest.fixture(autouse=True)
def _override_kb_deps(qdrant_memory, session_factory):
    app.dependency_overrides[get_qdrant] = lambda: qdrant_memory
    app.dependency_overrides[get_embedder_dep] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    _test_registry = IngestJobRegistry(
        session_factory=session_factory, client=qdrant_memory,
        embedder=_FakeEmbedder(), max_concurrent=2,
    )
    app.dependency_overrides[get_ingest_jobs] = lambda: _test_registry
    yield
    app.dependency_overrides.pop(get_qdrant, None)
    app.dependency_overrides.pop(get_embedder_dep, None)
    app.dependency_overrides.pop(get_session_factory, None)
    app.dependency_overrides.pop(get_ingest_jobs, None)


@pytest.fixture()
def demo_group(session_factory, demo_user) -> str:
    """M3: create a Group and add demo_user to it. Returns the group id.

    Exposed as a named fixture so tests that need the group_id can request it.
    The ``_demo_group_autouse`` fixture below wires it in for every test.
    """
    db = session_factory()
    grp = Group(name="default-group", default_tags=["kb-tag"])
    db.add(grp)
    db.flush()
    db.execute(user_groups.insert().values(user_id=demo_user["id"], group_id=grp.id))
    db.commit()
    gid = grp.id
    db.close()
    return gid


@pytest.fixture(autouse=True)
def _demo_group_autouse(demo_group):
    """Ensure every test in this file has the demo user in a group (M3 upload rule)."""


def _parse_sse_events(raw: bytes) -> list[dict]:
    events = []
    for frame in raw.decode("utf-8").split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: ")
        events.append(json.loads(frame[len("data: "):]))
    return events


def _upload(client, auth_headers, filename="notes.txt", content=b"Hello world. " * 200, content_type="text/plain"):
    return client.post(
        "/api/knowledge-base/upload",
        headers=auth_headers,
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


# --- Task 3.6 — upload (SSE) -------------------------------------------------


def test_upload_returns_event_stream_ending_with_file_resolved_then_done(client, auth_headers):
    r = _upload(client, auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(r.content)
    assert events, "expected at least one SSE event"
    assert events[-1] == {"type": "done"}
    resolved = events[-2]
    assert resolved["type"] == "file_resolved"
    assert resolved["file"]["status"] == "ready"
    assert resolved["file"]["chunkCount"] > 0

    # Earlier events are chunk_progress.
    for ev in events[:-2]:
        assert ev["type"] == "chunk_progress"


def test_uploaded_file_appears_in_list(client, auth_headers):
    r = _upload(client, auth_headers, filename="report.txt")
    assert r.status_code == 200
    events = _parse_sse_events(r.content)
    file_id = events[-2]["file"]["id"]

    r2 = client.get("/api/knowledge-base", headers=auth_headers)
    assert r2.status_code == 200
    body = r2.json()
    assert any(f["id"] == file_id for f in body)
    assert any(f["name"] == "report.txt" for f in body)


def test_upload_png_rejected_with_415(client, auth_headers):
    r = _upload(client, auth_headers, filename="image.png", content=b"\x89PNG fake", content_type="image/png")
    assert r.status_code == 415
    body = r.json()
    assert "code" in body and "message" in body


# --- Task 3.5 — list/filters --------------------------------------------------


def test_list_filters_by_search_status_tag(client, auth_headers):
    _upload(client, auth_headers, filename="alpha.txt", content=b"alpha content " * 100)
    _upload(client, auth_headers, filename="beta.txt", content=b"beta content " * 100)

    r = client.get("/api/knowledge-base", headers=auth_headers, params={"search": "alpha"})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()]
    assert names == ["alpha.txt"]

    r = client.get("/api/knowledge-base", headers=auth_headers, params={"status": "ready"})
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get("/api/knowledge-base", headers=auth_headers, params={"status": "error"})
    assert r.status_code == 200
    assert r.json() == []


def test_list_sorted_by_upload_date_desc(client, auth_headers):
    _upload(client, auth_headers, filename="first.txt")
    time.sleep(0.01)
    _upload(client, auth_headers, filename="second.txt")

    r = client.get("/api/knowledge-base", headers=auth_headers)
    names = [f["name"] for f in r.json()]
    assert names[0] == "second.txt"


def test_list_only_returns_caller_scope_kb_files(client, auth_headers, session_factory):
    """Cross-user isolation: another user's KB files never appear."""
    from app.auth.security import hash_password
    from app.models import User

    db = session_factory()
    other = User(email="other@example.com", display_name="Other", password_hash=hash_password("x"))
    db.add(other)
    db.commit()
    db.close()

    _upload(client, auth_headers, filename="mine.txt")

    r = client.get("/api/knowledge-base", headers=auth_headers)
    names = [f["name"] for f in r.json()]
    assert names == ["mine.txt"]


# --- Task 3.7 — tags / reindex / delete --------------------------------------


def test_update_tags_lowercases_and_dedupes(client, auth_headers):
    r = _upload(client, auth_headers, filename="tagme.txt")
    file_id = _parse_sse_events(r.content)[-2]["file"]["id"]

    r2 = client.patch(
        f"/api/knowledge-base/{file_id}/tags",
        headers=auth_headers,
        json={"tags": ["Finance", "finance", "HR", "hr", "Legal"]},
    )
    assert r2.status_code == 200
    assert sorted(r2.json()["tags"]) == ["finance", "hr", "legal"]


def test_update_tags_unknown_file_404(client, auth_headers):
    r = client.patch(
        "/api/knowledge-base/does-not-exist/tags", headers=auth_headers, json={"tags": ["x"]}
    )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_reindex_sets_indexing_status(client, auth_headers):
    r = _upload(client, auth_headers, filename="reindex-me.txt")
    file_id = _parse_sse_events(r.content)[-2]["file"]["id"]

    r2 = client.post(f"/api/knowledge-base/{file_id}/reindex", headers=auth_headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "indexing"
    assert body["chunkCount"] == 0


def test_delete_removes_row_and_qdrant_points(client, auth_headers, qdrant_memory):
    r = _upload(client, auth_headers, filename="deleteme.txt")
    file_id = _parse_sse_events(r.content)[-2]["file"]["id"]

    file_filter = qm.Filter(
        must=[qm.FieldCondition(key="file_id", match=qm.MatchValue(value=file_id))]
    )
    points_before, _ = qdrant_memory.scroll(COLLECTION, scroll_filter=file_filter, limit=100)
    assert len(points_before) > 0

    r2 = client.delete(f"/api/knowledge-base/{file_id}", headers=auth_headers)
    assert r2.status_code == 204

    r3 = client.get("/api/knowledge-base", headers=auth_headers)
    assert all(f["id"] != file_id for f in r3.json())

    points_after, _ = qdrant_memory.scroll(COLLECTION, scroll_filter=file_filter, limit=100)
    assert points_after == []


def test_delete_unknown_file_404(client, auth_headers):
    r = client.delete("/api/knowledge-base/does-not-exist", headers=auth_headers)
    assert r.status_code == 404


def test_upload_requires_auth(client):
    r = client.post(
        "/api/knowledge-base/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert r.status_code == 401
