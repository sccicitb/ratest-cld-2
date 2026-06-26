"""Chat SSE route tests (§7/§8.3) — fake `ModelClient`, no live LLM.

Mirrors `tests/test_chat_loop.py`'s fakes but drives the route end-to-end:
auth, ownership, SSE framing, and that persisted messages show up via the
existing `/messages` endpoint.
"""
from __future__ import annotations

import json

from app.chat.client import ModelChunk
from app.chat.routes import get_embedder_dep, get_model_client, get_qdrant
from app.main import app


class _FakeModelClient:
    """Each entry in `script` is the list of ModelChunks for one `.stream()` call."""

    def __init__(self, script: list[list[ModelChunk]]):
        self._script = list(script)

    async def stream(self, messages, tools):
        chunks = self._script.pop(0) if self._script else []
        for chunk in chunks:
            yield chunk


class _FakeEmbedder:
    def embed(self, texts):  # pragma: no cover - not exercised when tool runs are faked out
        return [[0.0] for _ in texts]


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


def test_chat_requires_auth(client):
    r = client.post("/api/sessions/does-not-matter/chat", json={"message": "hi"})
    assert r.status_code == 401


def test_chat_404_for_other_users_session(client, auth_headers, session_factory):
    from app.auth.security import hash_password
    from app.models import User

    db = session_factory()
    other = User(email="other@example.com", display_name="Other", password_hash=hash_password("x"))
    db.add(other)
    db.commit()
    from app.models import ChatSession

    other_session = ChatSession(user_id=other.id, title="New Chat")
    db.add(other_session)
    db.commit()
    other_session_id = other_session.id
    db.close()

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient([])
    try:
        r = client.post(
            f"/api/sessions/{other_session_id}/chat",
            headers=auth_headers,
            json={"message": "hi"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert r.headers["content-type"].startswith("application/json")


def test_chat_streams_plain_text_and_persists(client, auth_headers):
    sid = _create_session(client, auth_headers)

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        [[ModelChunk(type="text", text="Hello there!")]]
    )
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "hi, how are you?", "attachments": [{"id": "ignored"}]},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    assert events
    assert events[-1]["type"] == "done"
    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events) == "Hello there!"

    r = client.get(f"/api/sessions/{sid}/messages", headers=auth_headers)
    assert r.status_code == 200
    msgs = r.json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi, how are you?"
    assert msgs[1]["content"] == "Hello there!"
    assert msgs[1]["id"] == events[-1]["messageId"]


def test_chat_streams_tool_call_events(client, auth_headers):
    sid = _create_session(client, auth_headers)

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        [
            [
                ModelChunk(
                    type="tool_call",
                    id="call-1",
                    name="search_knowledge_base",
                    arguments={"query": "revenue"},
                )
            ],
            [ModelChunk(type="text", text="Here is your answer.")],
        ]
    )
    app.dependency_overrides[get_qdrant] = lambda: None
    app.dependency_overrides[get_embedder_dep] = lambda: _FakeEmbedder()

    # `search_knowledge_base` calls into `app.rag.retrieve.retrieve`, which we
    # don't want to hit a real Qdrant for in a route test — patch it to a
    # canned result instead.
    import app.tools.builtin.search_kb as search_kb_module

    original_retrieve = search_kb_module.retrieve
    search_kb_module.retrieve = lambda **kwargs: []

    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "what's our revenue?"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)
        app.dependency_overrides.pop(get_qdrant, None)
        app.dependency_overrides.pop(get_embedder_dep, None)
        search_kb_module.retrieve = original_retrieve

    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)

    calling_tool_events = [
        e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
    ]
    assert len(calling_tool_events) == 2
    active, complete = calling_tool_events
    assert active["status"] == "active"
    assert complete["status"] == "complete"
    assert active["toolName"] == "search_knowledge_base"

    assert events[-1]["type"] == "done"
    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events) == "Here is your answer."
