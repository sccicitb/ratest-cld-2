"""Chat HTTP routes (§7) — spawn/observe a detached chat turn as an SSE response.

The route itself does no chat-loop logic. `POST /{session_id}/chat` resolves
the session (owned-by check, same pattern as `app/sessions/routes.py`),
computes the display/model message content, then hands off to the
`ChatTurnRegistry` (`app/chat/turns.py`): `spawn()` starts the turn as a
detached background task (so a client disconnect doesn't cancel it), and the
route streams SSE frames by `observe()`-ing that task's replay log from index
0. `TurnInProgress` (a turn is already live for this session) maps to a 409.

`GET /{session_id}/stream` resumes/tails a turn already in flight for the
room — same `observe()` mechanism, from index 0 — or yields an empty stream
if the room is idle.

Stage 6: inline `attachments` with `ingested=False` by extracting their text
and prepending it to the message sent to the model.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.auth.deps import CurrentUser, DbSession
from app.chat.client import ModelClient, get_model_client
from app.chat.turns import ChatTurnRegistry, TurnInProgress
from app.errors import ApiError
from app.kb.routes import get_embedder_dep, get_qdrant
from app.models import Attachment, ChatSession
from app.rag.embedder import Embedder
from app.rag.extract import extract_text
from app.sse import sse

router = APIRouter()

QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder_dep)]
ModelClientDep = Annotated[ModelClient, Depends(get_model_client)]


def get_chat_turns(request: Request) -> ChatTurnRegistry:
    return request.app.state.chat_turns


ChatTurnsDep = Annotated[ChatTurnRegistry, Depends(get_chat_turns)]


class ChatRequest(BaseModel):
    message: str
    attachments: list[dict[str, Any]] | None = None


def _owned(db: DbSession, user_id: str, session_id: str) -> ChatSession:
    """Fetch a session or 404 — never leak another user's session (§5)."""
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise ApiError(404, "not_found", "Session not found")
    return s


@router.post("/{session_id}/chat")
async def chat(
    session_id: str,
    body: ChatRequest,
    user: CurrentUser,
    db: DbSession,
    chat_turns: ChatTurnsDep,
) -> StreamingResponse:
    # NOTE: this must be `async def`, not `def`. FastAPI runs sync path
    # operations in a worker thread (no running event loop there), but
    # `chat_turns.spawn()` calls `asyncio.create_task()` and needs one — the
    # same reason `kb/routes.py::upload_file` (which calls `ingest_jobs.spawn()`)
    # is `async def` too.
    _owned(db, user.id, session_id)

    # --- Stage 6: split display content (persisted/shown) from model content -
    # The user's bubble shows `body.message`; the model additionally sees the
    # text of any inline (non-ingested) attachments prepended. All attachment
    # ids — inline AND ingested — are linked to the message so their chips render.
    model_content = body.message
    attachment_ids: list[str] = []
    if body.attachments:
        inline_blocks: list[str] = []
        for att_ref in body.attachments:
            att_id = att_ref.get("id")
            if not att_id:
                continue
            attachment_ids.append(att_id)
            att = db.get(Attachment, att_id)
            if att is None or att.ingested:
                continue
            try:
                text = extract_text(att.url, att.file_name)
                inline_blocks.append(f"[Attached file: {att.file_name}]\n\n{text}")
            except Exception:
                # If we can't extract text from a blob we created, skip it
                # rather than failing the whole turn.
                pass
        if inline_blocks:
            model_content = "\n\n---\n\n".join(inline_blocks) + f"\n\n---\n\n{body.message}"

    try:
        chat_turns.spawn(
            session_id,
            user_id=user.id,
            message=body.message,
            model_content=model_content,
            attachment_ids=attachment_ids,
        )
    except TurnInProgress:
        raise ApiError(409, "turn_in_progress", "This chat already has a reply in progress")

    async def _stream():
        async for event in chat_turns.observe(session_id, from_index=0):
            yield sse(event)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{session_id}/stream")
def stream(
    session_id: str,
    user: CurrentUser,
    db: DbSession,
    chat_turns: ChatTurnsDep,
) -> StreamingResponse:
    _owned(db, user.id, session_id)

    async def _stream():
        if chat_turns.has_active(session_id):
            async for event in chat_turns.observe(session_id, from_index=0):
                yield sse(event)

    return StreamingResponse(_stream(), media_type="text/event-stream")
