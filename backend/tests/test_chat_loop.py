"""Chat tool-use loop tests (§7, Tasks 5.1-5.4) — a FAKE `ModelClient`, no live LLM.

The fake model is scripted per-call: each call to `.stream()` consumes the
next script entry (a list of `ModelChunk`s to yield). This lets tests drive
"zero tool calls", "one tool call then text", and "always a tool call" turns
deterministically.
"""
from __future__ import annotations

import asyncio

from app.chat.client import ModelChunk
from app.chat.events import done, error, step, token
from app.chat.loop import run_turn
from app.config import settings
from app.models import ChatSession, Message, User
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry


# --- Task 5.1 — event builders ----------------------------------------------


def test_step_event_full_fields():
    assert step("calling_tool", "active", id="abc", tool_name="search_knowledge_base",
                tool_args={"query": "x"}) == {
        "type": "step",
        "step": "calling_tool",
        "status": "active",
        "id": "abc",
        "toolName": "search_knowledge_base",
        "toolArgs": {"query": "x"},
    }


def test_step_event_omits_unset_optional_fields():
    assert step("thinking", "active") == {
        "type": "step",
        "step": "thinking",
        "status": "active",
    }


def test_token_event():
    assert token("hi") == {"type": "token", "content": "hi"}


def test_done_event():
    assert done("m1") == {"type": "done", "messageId": "m1"}


def test_error_event():
    assert error("boom") == {"type": "error", "message": "boom"}


# --- Fakes -------------------------------------------------------------------


class _FakeModelClient:
    """Each entry in `script` is the list of ModelChunks for one `.stream()` call."""

    def __init__(self, script: list[list[ModelChunk]]):
        self._script = list(script)
        self.calls: list[tuple[list[dict], list[dict]]] = []

    async def stream(self, messages, tools):
        self.calls.append((messages, tools))
        if self._script:
            chunks = self._script.pop(0)
        else:
            chunks = []
        for chunk in chunks:
            yield chunk


class _AlwaysToolCallModelClient:
    """Always yields the same tool call, regardless of whether tools are offered."""

    def __init__(self):
        self.call_count = 0

    async def stream(self, messages, tools):
        self.call_count += 1
        if not tools:
            # Cap was hit and no tools offered: behave like a real model and
            # produce a final text answer instead.
            yield ModelChunk(type="text", text="forced final answer")
            return
        yield ModelChunk(
            type="tool_call", id=f"call-{self.call_count}",
            name="fake_tool", arguments={"x": self.call_count},
        )


class _FakeTool:
    name = "fake_tool"

    def __init__(self):
        self.calls: list[dict] = []

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": "fake_tool"}}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        self.calls.append(args)
        return "fake tool result"


def _make_session(session_factory) -> tuple[object, ChatSession]:
    db = session_factory()
    user = User(email="u@example.com", display_name="U", password_hash="x")
    db.add(user)
    db.commit()
    session = ChatSession(user_id=user.id, title="New Chat")
    db.add(session)
    db.commit()
    return db, session


def _ctx(db, session: ChatSession) -> ToolContext:
    return ToolContext(
        user_id=session.user_id, session_id=session.id, db=db, client=None, embedder=None
    )


def _registry(tool=None) -> ToolRegistry:
    registry = ToolRegistry()
    if tool is not None:
        registry.register(tool)
    return registry


async def _collect(gen):
    return [event async for event in gen]


# --- Task 5.2 — zero tool calls ----------------------------------------------


def test_run_turn_zero_tool_calls_streams_tokens_and_persists(session_factory):
    db, session = _make_session(session_factory)
    model = _FakeModelClient([[ModelChunk(type="text", text="Hello there!")]])
    registry = _registry()
    ctx = _ctx(db, session)

    events = asyncio.run(
        _collect(run_turn(
            db=db, session=session, message="hi, how are you?",
            registry=registry, model=model, ctx=ctx,
        ))
    )

    assert events[0] == step("thinking", "active")
    assert step("thinking", "complete") in events
    assert step("generating_response", "active") in events
    assert step("generating_response", "complete") in events
    token_events = [e for e in events if e["type"] == "token"]
    assert len(token_events) >= 1
    assert "".join(e["content"] for e in token_events) == "Hello there!"
    assert events[-1]["type"] == "done"

    messages = db.query(Message).filter_by(session_id=session.id).order_by(Message.created_at).all()
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hi, how are you?"
    assert messages[1].content == "Hello there!"
    assert messages[1].id == events[-1]["messageId"]

    db.refresh(session)
    assert session.title == "hi, how are you?"


def test_run_turn_auto_titles_long_first_message(session_factory):
    db, session = _make_session(session_factory)
    long_message = "a" * 60
    model = _FakeModelClient([[ModelChunk(type="text", text="ok")]])
    events = asyncio.run(
        _collect(run_turn(
            db=db, session=session, message=long_message,
            registry=_registry(), model=model, ctx=_ctx(db, session),
        ))
    )
    assert events  # sanity
    db.refresh(session)
    assert session.title == "a" * 40 + "…"
    assert len(session.title) == 41


# --- Task 5.3 — one tool call -------------------------------------------------


def test_run_turn_one_tool_call_then_text(session_factory):
    db, session = _make_session(session_factory)
    tool = _FakeTool()
    model = _FakeModelClient([
        [ModelChunk(type="tool_call", id="call-1", name="fake_tool", arguments={"query": "revenue"})],
        [ModelChunk(type="text", text="Here is your answer.")],
    ])
    registry = _registry(tool)
    ctx = _ctx(db, session)

    events = asyncio.run(
        _collect(run_turn(
            db=db, session=session, message="what's our revenue?",
            registry=registry, model=model, ctx=ctx,
        ))
    )

    calling_tool_events = [e for e in events if e["type"] == "step" and e["step"] == "calling_tool"]
    assert len(calling_tool_events) == 2
    active, complete = calling_tool_events
    assert active["status"] == "active"
    assert complete["status"] == "complete"
    assert active["id"] == complete["id"]
    assert active["toolName"] == "fake_tool"
    assert active["toolArgs"] == {"query": "revenue", "scope": "this chat + KB"}

    # The tool actually ran.
    assert tool.calls == [{"query": "revenue"}]

    # Text was NOT streamed on the tool-calling turn (only one token sequence,
    # from the final no-tool-call turn).
    token_events = [e for e in events if e["type"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["content"] == "Here is your answer."

    assert events[-1]["type"] == "done"

    # Second model call included the tool's result as a `role: tool` message.
    second_call_messages = model.calls[1][0]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"] == "fake tool result"
    assert tool_messages[0]["tool_call_id"] == "call-1"


def test_run_turn_scope_is_kb_only_without_session(session_factory):
    db, session = _make_session(session_factory)
    tool = _FakeTool()
    model = _FakeModelClient([
        [ModelChunk(type="tool_call", id="call-1", name="fake_tool", arguments={"query": "x"})],
        [ModelChunk(type="text", text="done")],
    ])
    ctx = ToolContext(user_id=session.user_id, session_id=None, db=db, client=None, embedder=None)

    events = asyncio.run(
        _collect(run_turn(
            db=db, session=session, message="q",
            registry=_registry(tool), model=model, ctx=ctx,
        ))
    )
    active = next(e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
                  and e["status"] == "active")
    assert active["toolArgs"]["scope"] == "KB"


# --- Task 5.4 — iteration cap --------------------------------------------------


def test_run_turn_stops_after_max_tool_iterations(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "max_tool_iterations", 3)
    db, session = _make_session(session_factory)
    registry = _registry(_FakeTool())
    model = _AlwaysToolCallModelClient()
    ctx = _ctx(db, session)

    events = asyncio.run(
        _collect(run_turn(
            db=db, session=session, message="loop forever?",
            registry=registry, model=model, ctx=ctx,
        ))
    )

    # 3 tool rounds offered (cap), then a 4th call with no tools forces the
    # final answer.
    assert model.call_count == 4
    calling_tool_active = [
        e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
        and e["status"] == "active"
    ]
    assert len(calling_tool_active) == 3

    assert events[-1]["type"] == "done"
    token_events = [e for e in events if e["type"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["content"] == "forced final answer"

    messages = db.query(Message).filter_by(session_id=session.id).order_by(Message.created_at).all()
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "forced final answer"


# --- Error path ----------------------------------------------------------------


def test_run_turn_emits_error_event_on_exception(session_factory):
    db, session = _make_session(session_factory)

    class _BoomModel:
        async def stream(self, messages, tools):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover - makes this an async generator

    events = asyncio.run(
        _collect(run_turn(
            db=db, session=session, message="hi",
            registry=_registry(), model=_BoomModel(), ctx=_ctx(db, session),
        ))
    )
    assert events[-1]["type"] == "error"
    assert "model exploded" in events[-1]["message"]


def test_run_turn_yields_reasoning_before_final_token(session_factory):
    db, session = _make_session(session_factory)
    model = _FakeModelClient([[
        ModelChunk(type="reasoning", text="Let me think."),
        ModelChunk(type="text", text="Final answer."),
    ]])
    registry = _registry()
    ctx = _ctx(db, session)

    events = asyncio.run(_collect(run_turn(
        db=db, session=session, message="q",
        registry=registry, model=model, ctx=ctx,
    )))

    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    assert reasoning_events == [{"type": "reasoning", "content": "Let me think."}]
    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events) == "Final answer."
    # reasoning arrives before the final answer token
    assert events.index(reasoning_events[0]) < events.index(token_events[0])
