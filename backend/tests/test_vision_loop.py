"""Vision loop tests (v1.1-V2): image_url content blocks, cross-turn re-feed, cap.

Tests are purely unit/integration-level — fake model client, in-memory DB,
real blob storage pointing at a tmp_path.  No live LLM required.
"""
from __future__ import annotations

import asyncio
import base64

from app.chat.client import ModelChunk
from app.chat.loop import _cap_images, run_turn
from app.config import settings
from app.models import Attachment, ChatSession, User
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_chat_loop.py)
# ---------------------------------------------------------------------------


def _make_session(session_factory):
    db = session_factory()
    user = User(email="vision@example.com", display_name="V", password_hash="x")
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


def _registry() -> ToolRegistry:
    return ToolRegistry()


async def _collect(gen):
    return [event async for event in gen]


def _make_image_blob(tmp_path, key: str = "img.png", data: bytes = b"\x89PNG fake") -> str:
    (tmp_path / key).write_bytes(data)
    return key


def _make_att(db, blob_key: str, file_type: str = "image/png") -> Attachment:
    att = Attachment(
        file_name=blob_key,
        file_type=file_type,
        file_size=10,
        url=blob_key,
        ingested=False,
    )
    db.add(att)
    db.commit()
    return att


class _CaptureModel:
    """Records every (messages, tools) pair passed to .stream()."""

    def __init__(self, reply: str = "ok"):
        self._reply = reply
        self.calls: list[list[dict]] = []

    async def stream(self, messages, tools):
        self.calls.append(list(messages))
        yield ModelChunk(type="text", text=self._reply)


# ---------------------------------------------------------------------------
# _cap_images — pure unit tests
# ---------------------------------------------------------------------------


def test_cap_images_noop_under_budget():
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]},
    ]
    result = _cap_images(msgs, cap=6)
    assert result == msgs


def test_cap_images_drops_oldest_keeps_newest():
    # 3 messages each with 1 image; cap=2 → drop the first (oldest).
    def _img(tag):
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tag}"}}

    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "t1"}, _img("img1")]},
        {"role": "assistant", "content": "reply1"},
        {"role": "user", "content": [{"type": "text", "text": "t2"}, _img("img2")]},
        {"role": "assistant", "content": "reply2"},
        {"role": "user", "content": [{"type": "text", "text": "t3"}, _img("img3")]},
    ]
    result = _cap_images(msgs, cap=2)

    # First user msg loses its image block → collapsed to plain string.
    assert result[0]["content"] == "t1"
    # Second user msg keeps its image.
    assert isinstance(result[2]["content"], list)
    kept_urls = [
        b["image_url"]["url"]
        for m in result
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "image_url"
    ]
    assert set(kept_urls) == {"data:image/png;base64,img2", "data:image/png;base64,img3"}


def test_cap_images_collapses_emptied_message_to_plain_string():
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,old"}},
        ]},
        {"role": "user", "content": [
            {"type": "text", "text": "follow-up"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,new"}},
        ]},
    ]
    result = _cap_images(msgs, cap=1)
    # Oldest image dropped → first msg collapses to plain string.
    assert result[0]["content"] == "hello"
    # Newest kept as content array.
    assert isinstance(result[1]["content"], list)


def test_cap_images_exact_budget_kept():
    def _img(tag):
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tag}"}}

    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "a"}, _img("a"), _img("b")]},
    ]
    result = _cap_images(msgs, cap=2)
    assert result == msgs  # exactly at budget, no change


# ---------------------------------------------------------------------------
# run_turn: text-only turn stays plain string (regression guard)
# ---------------------------------------------------------------------------


def test_no_image_turn_content_is_plain_string(session_factory):
    db, session = _make_session(session_factory)
    model = _CaptureModel("answer")
    events = asyncio.run(_collect(run_turn(
        db=db, session=session, message="hello world",
        registry=_registry(), model=model, ctx=_ctx(db, session),
    )))
    assert events[-1]["type"] == "done"

    user_msgs = [m for m in model.calls[0] if m.get("role") == "user"]
    assert len(user_msgs) == 1
    assert isinstance(user_msgs[0]["content"], str)
    assert user_msgs[0]["content"] == "hello world"


# ---------------------------------------------------------------------------
# run_turn: current-turn image → content list with image_url block
# ---------------------------------------------------------------------------


def test_current_turn_image_builds_content_array(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))

    db, session = _make_session(session_factory)
    blob_key = _make_image_blob(tmp_path, "cat.png", b"\x89PNG cat data")
    att = _make_att(db, blob_key, "image/png")

    model = _CaptureModel("I see a PNG")
    events = asyncio.run(_collect(run_turn(
        db=db, session=session, message="what is this?",
        registry=_registry(), model=model, ctx=_ctx(db, session),
        attachment_ids=[att.id],
    )))
    assert events[-1]["type"] == "done"

    user_msgs = [m for m in model.calls[0] if m.get("role") == "user"]
    assert len(user_msgs) == 1
    content = user_msgs[0]["content"]

    assert isinstance(content, list), "expected content array for image turn"
    text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
    img_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "image_url"]
    assert len(text_blocks) == 1
    assert text_blocks[0]["text"] == "what is this?"
    assert len(img_blocks) == 1
    url = img_blocks[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # Verify the actual base64 content matches what we wrote.
    expected_b64 = base64.b64encode(b"\x89PNG cat data").decode()
    assert url == f"data:image/png;base64,{expected_b64}"


# ---------------------------------------------------------------------------
# run_turn: non-image attachment does NOT become an image block
# ---------------------------------------------------------------------------


def test_non_image_attachment_not_in_content_array(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))

    db, session = _make_session(session_factory)
    # A text file — not an image.
    blob_key = "note.txt"
    (tmp_path / blob_key).write_bytes(b"hello text")
    att = _make_att(db, blob_key, "text/plain")

    model = _CaptureModel("noted")
    events = asyncio.run(_collect(run_turn(
        db=db, session=session, message="summarize",
        registry=_registry(), model=model, ctx=_ctx(db, session),
        attachment_ids=[att.id],
    )))
    assert events[-1]["type"] == "done"

    user_msgs = [m for m in model.calls[0] if m.get("role") == "user"]
    # Text attachment → no image block → content stays a plain string.
    assert isinstance(user_msgs[0]["content"], str)


# ---------------------------------------------------------------------------
# run_turn: persistence — turn 1 has image; turn 2 has no image.
# Turn-2's captured messages must still contain turn-1's image block.
# ---------------------------------------------------------------------------


def test_image_persists_across_turns(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))

    db, session = _make_session(session_factory)
    blob_key = _make_image_blob(tmp_path, "chart.png", b"\x89PNG chart data")
    att = _make_att(db, blob_key, "image/png")

    # --- Turn 1: send the image ---
    model1 = _CaptureModel("I see a chart")
    events1 = asyncio.run(_collect(run_turn(
        db=db, session=session, message="describe this chart",
        registry=_registry(), model=model1, ctx=_ctx(db, session),
        attachment_ids=[att.id],
    )))
    assert events1[-1]["type"] == "done"

    # Reload session so its .messages relationship reflects the committed turn-1 rows.
    db.refresh(session)

    # --- Turn 2: no new image ---
    model2 = _CaptureModel("the chart has blue bars")
    events2 = asyncio.run(_collect(run_turn(
        db=db, session=session, message="what color are the bars?",
        registry=_registry(), model=model2, ctx=_ctx(db, session),
    )))
    assert events2[-1]["type"] == "done"

    # Turn-2's messages list: history[0] is the turn-1 user msg (with image).
    messages_t2 = model2.calls[0]
    # Find user messages in history order.
    user_msgs = [m for m in messages_t2 if m.get("role") == "user"]
    # There should be 2 user messages: turn-1 (with image) and turn-2 (plain).
    assert len(user_msgs) == 2

    # Turn-1 (history): content should be a list with an image_url block.
    content_t1 = user_msgs[0]["content"]
    assert isinstance(content_t1, list), "turn-1 history should re-emit image block"
    img_blocks_t1 = [b for b in content_t1 if isinstance(b, dict) and b.get("type") == "image_url"]
    assert len(img_blocks_t1) == 1
    assert img_blocks_t1[0]["image_url"]["url"].startswith("data:image/png;base64,")

    # Turn-2 (current): plain string — no new image.
    content_t2 = user_msgs[1]["content"]
    assert isinstance(content_t2, str)
    assert content_t2 == "what color are the bars?"


# ---------------------------------------------------------------------------
# run_turn: cap — monkeypatch max_vision_images_per_turn=1; 2 image turns →
# only 1 (newest) image block survives in the assembled messages.
# ---------------------------------------------------------------------------


def test_cap_limits_image_blocks_to_newest(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))
    monkeypatch.setattr(settings, "max_vision_images_per_turn", 1)

    db, session = _make_session(session_factory)

    # Two separate image blobs.
    blob1 = _make_image_blob(tmp_path, "old.png", b"\x89PNG old")
    blob2 = _make_image_blob(tmp_path, "new.png", b"\x89PNG new")
    att1 = _make_att(db, blob1, "image/png")

    # Turn 1: first image.
    model1 = _CaptureModel("old image")
    events1 = asyncio.run(_collect(run_turn(
        db=db, session=session, message="turn one",
        registry=_registry(), model=model1, ctx=_ctx(db, session),
        attachment_ids=[att1.id],
    )))
    assert events1[-1]["type"] == "done"

    db.refresh(session)

    att2 = _make_att(db, blob2, "image/png")

    # Turn 2: second image.
    model2 = _CaptureModel("new image")
    events2 = asyncio.run(_collect(run_turn(
        db=db, session=session, message="turn two",
        registry=_registry(), model=model2, ctx=_ctx(db, session),
        attachment_ids=[att2.id],
    )))
    assert events2[-1]["type"] == "done"

    # At most 1 image block in the messages sent to the model on turn 2.
    messages_t2 = model2.calls[0]
    all_img_blocks = [
        b
        for m in messages_t2
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "image_url"
    ]
    assert len(all_img_blocks) == 1, f"expected 1 image block (cap=1), got {len(all_img_blocks)}"

    # The surviving block should be the NEWER image (base64 of "new.png" blob).
    expected_b64 = base64.b64encode(b"\x89PNG new").decode()
    assert all_img_blocks[0]["image_url"]["url"] == f"data:image/png;base64,{expected_b64}"
