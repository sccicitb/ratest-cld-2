"""Chat SSE route tests (§7/§8.3) — fake `ModelClient`, no live LLM.

Mirrors `tests/test_chat_loop.py`'s fakes but drives the route end-to-end:
auth, ownership, SSE framing, and that persisted messages show up via the
existing `/messages` endpoint.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from app.chat.client import ModelChunk
from app.chat.routes import get_chat_turns
from app.chat.turns import ChatTurnRegistry
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

    def embed_passages(self, texts):
        return [
            {"dense": [0.1] * 1024, "sparse": {"indices": [1, 2], "values": [1.0, 0.5]}}
            for _ in texts
        ]

    def embed_query(self, text):
        return {"dense": [0.1] * 1024, "sparse": {"indices": [1, 2], "values": [1.0, 0.5]}}


def _override_chat_turns(script, session_factory, *, client=None, embedder=None):
    """Wire a real ChatTurnRegistry with a fake model + test session_factory,
    mirroring how test_kb.py overrides get_ingest_jobs."""
    registry = ChatTurnRegistry(
        model=_FakeModelClient(script),
        client=client,
        embedder=embedder if embedder is not None else _FakeEmbedder(),
        session_factory=session_factory,
        max_concurrent=4,
    )
    app.dependency_overrides[get_chat_turns] = lambda: registry
    return registry


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

    _override_chat_turns([], session_factory)
    try:
        r = client.post(
            f"/api/sessions/{other_session_id}/chat",
            headers=auth_headers,
            json={"message": "hi"},
        )
    finally:
        app.dependency_overrides.pop(get_chat_turns, None)

    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert r.headers["content-type"].startswith("application/json")


def test_chat_streams_plain_text_and_persists(client, auth_headers, session_factory):
    sid = _create_session(client, auth_headers)

    _override_chat_turns([[ModelChunk(type="text", text="Hello there!")]], session_factory)
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "hi, how are you?", "attachments": [{"id": "ignored"}]},
        )
    finally:
        app.dependency_overrides.pop(get_chat_turns, None)

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


def test_chat_streams_tool_call_events(client, auth_headers, session_factory):
    sid = _create_session(client, auth_headers)

    _override_chat_turns(
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
        ],
        session_factory,
    )

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
        app.dependency_overrides.pop(get_chat_turns, None)
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


def _make_login_user(session_factory, email: str, password: str) -> None:
    from app.auth.security import hash_password
    from app.models import User

    db = session_factory()
    db.add(User(email=email, display_name="Concurrency", password_hash=hash_password(password)))
    db.commit()
    db.close()


async def _login_and_create_session(ac: httpx.AsyncClient, email: str, password: str) -> tuple[str, dict]:
    r = await ac.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['accessToken']}"}
    r2 = await ac.post("/api/sessions", headers=headers)
    assert r2.status_code == 201, r2.text
    return r2.json()["id"], headers


def test_concurrent_chat_returns_409(session_factory):
    """A second POST while a turn is still live must 409, not spawn a duplicate.

    Drives everything (login, session creation, both chat POSTs) through one
    `httpx.AsyncClient` over `httpx.ASGITransport` on a single asyncio loop —
    mirroring `test_chat_turns.py`'s technique. Deliberately does NOT mix in
    the `client` (TestClient) fixture: TestClient runs the ASGI app on its own
    dedicated portal thread, and interleaving that with this test's own
    `asyncio.run()` loop caused the shared StaticPool sqlite connection to be
    touched cross-thread, which hung the suite. Keeping every DB touch on one
    thread/loop avoids that.
    """
    from app.db import get_db

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    _make_login_user(session_factory, "conc1@test.com", "pw123456")

    async def _run():
        gate = asyncio.Event()
        started = asyncio.Event()

        class _GatedModel:
            async def stream(self, messages, tools):
                yield ModelChunk(type="text", text="partial")
                started.set()
                await gate.wait()

        registry = ChatTurnRegistry(
            model=_GatedModel(), client=None, embedder=_FakeEmbedder(),
            session_factory=session_factory, max_concurrent=4,
        )
        app.dependency_overrides[get_chat_turns] = lambda: registry

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            sid, headers = await _login_and_create_session(ac, "conc1@test.com", "pw123456")

            first_task = asyncio.create_task(
                ac.post(f"/api/sessions/{sid}/chat", headers=headers, json={"message": "hi"})
            )
            await asyncio.wait_for(started.wait(), timeout=8)

            r2 = await asyncio.wait_for(
                ac.post(f"/api/sessions/{sid}/chat", headers=headers, json={"message": "again"}),
                timeout=8,
            )
            assert r2.status_code == 409
            assert r2.json()["code"] == "turn_in_progress"

            gate.set()
            r1 = await asyncio.wait_for(first_task, timeout=8)
            assert r1.status_code == 200

    app.dependency_overrides[get_db] = override_get_db
    try:
        asyncio.run(_run())
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_chat_turns, None)


def test_stream_replays_live_turn_and_empty_when_idle(session_factory):
    """GET /stream replays a live turn's log, and is an empty stream when idle.

    Everything — login, both sessions, the gated turn, both /stream calls —
    runs through one `httpx.AsyncClient`/`asyncio.run()` loop. Deliberately
    avoids the `client` (TestClient) fixture: see `test_concurrent_chat_returns_409`
    for why mixing TestClient's own portal thread with a second, separately
    driven event loop hung this suite even without genuine concurrent access.

    Also deliberately does NOT read the live /stream response incrementally
    and break out early: `httpx.ASGITransport` is a lightweight test double
    that doesn't appear to signal an early consumer disconnect back to the
    ASGI app (no real `http.disconnect`), so Starlette's send side blocks
    forever waiting for a reader that stopped reading. Instead both the POST
    and the concurrent GET /stream run as background tasks to natural
    completion once the gate releases.
    """
    from app.db import get_db

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    _make_login_user(session_factory, "conc2@test.com", "pw123456")

    async def _run():
        gate = asyncio.Event()
        started = asyncio.Event()

        class _GatedModel:
            async def stream(self, messages, tools):
                yield ModelChunk(type="text", text="early")
                started.set()
                await gate.wait()
                yield ModelChunk(type="text", text="-late")

        registry = ChatTurnRegistry(
            model=_GatedModel(), client=None, embedder=_FakeEmbedder(),
            session_factory=session_factory, max_concurrent=4,
        )
        app.dependency_overrides[get_chat_turns] = lambda: registry

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/auth/login", json={"email": "conc2@test.com", "password": "pw123456"})
            assert r.status_code == 200, r.text
            headers = {"Authorization": f"Bearer {r.json()['accessToken']}"}

            r_sess = await ac.post("/api/sessions", headers=headers)
            assert r_sess.status_code == 201, r_sess.text
            sid = r_sess.json()["id"]

            turn_task = asyncio.create_task(
                ac.post(f"/api/sessions/{sid}/chat", headers=headers, json={"message": "hi"})
            )
            await asyncio.wait_for(started.wait(), timeout=8)

            # Live room: attach a second observer (resume) while the turn is
            # still running — it must replay "early" (already published) then
            # tail "-late" once the gate releases. Let it run to natural
            # completion rather than reading a partial body.
            stream_task = asyncio.create_task(
                ac.get(f"/api/sessions/{sid}/stream", headers=headers)
            )
            await asyncio.sleep(0.05)  # let stream_task attach and observe from index 0

            gate.set()
            r1 = await asyncio.wait_for(turn_task, timeout=8)
            assert r1.status_code == 200
            r_stream = await asyncio.wait_for(stream_task, timeout=8)
            assert r_stream.status_code == 200

            events = _parse_sse(r_stream.text)
            token_events = [e for e in events if e["type"] == "token"]
            assert "".join(e["content"] for e in token_events) == "early-late"

            # Idle room: a session that never had a turn -> empty stream.
            r_sess2 = await ac.post("/api/sessions", headers=headers)
            assert r_sess2.status_code == 201, r_sess2.text
            sid2 = r_sess2.json()["id"]
            r_empty = await ac.get(f"/api/sessions/{sid2}/stream", headers=headers)
            assert r_empty.status_code == 200
            assert _parse_sse(r_empty.text) == []

    app.dependency_overrides[get_db] = override_get_db
    try:
        asyncio.run(_run())
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_chat_turns, None)
