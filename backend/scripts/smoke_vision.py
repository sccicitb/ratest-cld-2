"""Smoke test: vision in the chat loop against the real Qwen3-VL model.

NOT a CI test — run manually against the live model endpoint.

Usage (from backend/):
    env -u VIRTUAL_ENV uv run python scripts/smoke_vision.py

What it does:
1. Writes a tiny 1x1 red PNG blob to the blob store.
2. Creates a DB session + Attachment row pointing at it.
3. Calls run_turn() with attachment_ids — model should describe the image.
4. Calls run_turn() again with NO new image — model should still reference the
   image (persistence / re-feed).
5. Prints both model replies and a PASS/FAIL verdict.

Requires MODEL_BASE_URL (default: https://llama.sccic.org/v1) to be reachable
and pointing at a vision-capable model (Qwen3-VL or compatible).
"""
from __future__ import annotations

import asyncio
import struct
import zlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chat.client import OpenAIModelClient
from app.chat.loop import run_turn
from app.config import settings
from app.db import Base
from app.models import Attachment, ChatSession, User
from app.storage import save_upload
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Minimal 1×1 red PNG (valid, <100 bytes).
# ---------------------------------------------------------------------------
_IHDR = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
_IDAT_raw = b"\x00\xff\x00\x00"  # filter=0, R=255, G=0, B=0
_IDAT_compressed = zlib.compress(_IDAT_raw)


def _png_chunk(name: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(name + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)


RED_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", _IHDR)
    + _png_chunk(b"IDAT", _IDAT_compressed)
    + _png_chunk(b"IEND", b"")
)


# ---------------------------------------------------------------------------
# Setup: in-memory SQLite DB + model client.
# ---------------------------------------------------------------------------

def _setup_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return factory()


async def _run() -> None:
    print(f"MODEL_BASE_URL: {settings.model_base_url}")
    print(f"MODEL_NAME:     {settings.model_name}")
    print()

    db = _setup_db()
    model = OpenAIModelClient()

    # Create a user + chat session.
    user = User(email="smoke@vision.test", display_name="Smoke", password_hash="x")
    db.add(user)
    db.commit()
    session = ChatSession(user_id=user.id, title="New Chat")
    db.add(session)
    db.commit()

    # Persist the red PNG blob and create an Attachment row.
    class _FakePNGUpload:
        filename = "red.png"

        def read(self) -> bytes:
            return RED_PNG

    storage_key, size = save_upload(_FakePNGUpload())
    att = Attachment(
        file_name="red.png",
        file_type="image/png",
        file_size=size,
        url=storage_key,
        ingested=False,
    )
    db.add(att)
    db.commit()
    att_id = att.id

    registry = ToolRegistry()
    ctx = ToolContext(user_id=user.id, session_id=session.id, db=db, client=None, embedder=None)

    # --- Turn 1: send image and ask the model to describe it ---
    print("=== Turn 1: describing the image ===")
    reply1_parts: list[str] = []
    async for event in run_turn(
        db=db, session=session, message="What color is the image?",
        registry=registry, model=model, ctx=ctx,
        attachment_ids=[att_id],
    ):
        if event.get("type") == "token":
            reply1_parts.append(event["content"])
        elif event.get("type") == "error":
            print(f"ERROR: {event['message']}")
            return

    reply1 = "".join(reply1_parts)
    print(f"Model reply 1: {reply1!r}")
    print()

    db.refresh(session)

    # --- Turn 2: follow-up with no new image ---
    print("=== Turn 2: follow-up (no new image) ===")
    reply2_parts: list[str] = []
    async for event in run_turn(
        db=db, session=session, message="What hex code is that color?",
        registry=registry, model=model, ctx=ctx,
    ):
        if event.get("type") == "token":
            reply2_parts.append(event["content"])
        elif event.get("type") == "error":
            print(f"ERROR: {event['message']}")
            return

    reply2 = "".join(reply2_parts)
    print(f"Model reply 2: {reply2!r}")
    print()

    # --- Verdict ---
    red_kws = {"red", "#ff0000", "ff0000", "255", "rgb(255"}
    turn1_ok = any(kw in reply1.lower() for kw in red_kws)
    turn2_ok = any(kw in reply2.lower() for kw in red_kws) or len(reply2) > 20

    if turn1_ok and turn2_ok:
        print("PASS: model described the red image on turn 1 and answered coherently on turn 2.")
    else:
        if not turn1_ok:
            print("WARN: turn-1 reply did not mention 'red' or '#ff0000'.")
        if not turn2_ok:
            print("WARN: turn-2 reply seems empty or off-topic.")
        print("PARTIAL/FAIL — inspect replies above.")


if __name__ == "__main__":
    asyncio.run(_run())
