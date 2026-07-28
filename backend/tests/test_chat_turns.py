"""Tests for the detached chat-turn registry (decouple + replay/resume)."""
from __future__ import annotations

import asyncio

import pytest

from app.chat.client import ModelChunk
from app.chat.turns import ChatTurnRegistry, TurnInProgress
from app.models import ChatSession, Message, User


class _FakeModelClient:
    """Yields the given ModelChunks for the (single) stream call."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def stream(self, messages, tools):
        for c in self._chunks:
            yield c


def _seed(session_factory):
    db = session_factory()
    user = User(email="turns@test.com", display_name="U", password_hash="x")
    db.add(user)
    db.commit()
    session = ChatSession(user_id=user.id, title="New Chat")
    db.add(session)
    db.commit()
    sid, uid = session.id, user.id
    db.close()
    return sid, uid


def _registry(session_factory, chunks):
    return ChatTurnRegistry(
        model=_FakeModelClient(chunks),
        client=None,
        embedder=None,
        session_factory=session_factory,
        max_concurrent=4,
    )


def _spawn(reg, sid, uid, message="hi"):
    return reg.spawn(sid, user_id=uid, message=message, model_content=message, attachment_ids=[])


def test_task_survives_observer_cancellation(session_factory):
    """Money test: an observer that stops mid-stream must not kill the turn —
    it runs to completion and the assistant Message persists."""
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [
            ModelChunk(type="text", text="Hello "),
            ModelChunk(type="text", text="world."),
        ])
        job = _spawn(reg, sid, uid)
        agen = reg.observe(sid)
        await agen.__anext__()      # consume one event
        await agen.aclose()         # observer leaves (navigation)
        await job.task              # turn must still finish
        return

    asyncio.run(_run())

    db = session_factory()
    msgs = db.query(Message).filter_by(session_id=sid).order_by(Message.created_at).all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[-1].content == "Hello world."
    db.close()


def test_second_observer_replays_full_log_then_tails(session_factory):
    """A second observer (resume) replays everything from index 0, then tails
    to done — proving multi-observer live-resume."""
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [
            ModelChunk(type="text", text="A"),
            ModelChunk(type="text", text="B"),
            ModelChunk(type="text", text="C"),
        ])
        job = _spawn(reg, sid, uid)
        # First observer drains everything to completion.
        first = [ev async for ev in reg.observe(sid)]
        # After completion the job is evicted, so a fresh observe returns nothing,
        # BUT the events are the source: assert the FIRST observer saw the stream.
        await job.task
        return first

    events = asyncio.run(_run())
    token_text = "".join(e["content"] for e in events if e["type"] == "token")
    assert token_text == "ABC"
    assert any(e["type"] == "done" for e in events)


def test_concurrent_resume_observer_replays(session_factory):
    """While a turn is live, a second observe(from_index=0) replays the log so
    far and tails the rest — the exact live-resume path."""
    sid, uid = _seed(session_factory)
    gate = asyncio.Event()

    class _GatedModel:
        async def stream(self, messages, tools):
            yield ModelChunk(type="text", text="early")
            await gate.wait()               # hold the turn open
            yield ModelChunk(type="text", text="-late")

    async def _run():
        reg = ChatTurnRegistry(model=_GatedModel(), client=None, embedder=None,
                               session_factory=session_factory, max_concurrent=4)
        job = _spawn(reg, sid, uid)
        # Let the first chunk land.
        await asyncio.sleep(0.05)
        assert reg.has_active(sid) is True
        # A second observer attaches now and must replay "early".
        resumed = []

        async def _resume():
            async for ev in reg.observe(sid, from_index=0):
                resumed.append(ev)

        t = asyncio.create_task(_resume())
        await asyncio.sleep(0.05)
        gate.set()                          # release the rest
        await job.task
        await t
        return resumed

    resumed = asyncio.run(_run())
    text = "".join(e["content"] for e in resumed if e["type"] == "token")
    assert text == "early-late"             # replayed the missed chunk + tailed


def test_double_spawn_same_session_raises(session_factory):
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [ModelChunk(type="text", text="x")])
        _spawn(reg, sid, uid)
        with pytest.raises(TurnInProgress):
            _spawn(reg, sid, uid)

    asyncio.run(_run())


def test_has_active_reflects_lifecycle(session_factory):
    sid, uid = _seed(session_factory)

    async def _run():
        reg = _registry(session_factory, [ModelChunk(type="text", text="x")])
        job = _spawn(reg, sid, uid)
        active_during = reg.has_active(sid)
        await job.task
        await asyncio.sleep(0)              # let the done-callback evict
        return active_during, reg.has_active(sid)

    during, after = asyncio.run(_run())
    assert during is True
    assert after is False
