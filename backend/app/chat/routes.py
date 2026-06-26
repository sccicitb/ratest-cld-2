"""Chat HTTP route (§7) — wraps `run_turn` in an SSE response.

The route itself does no chat-loop logic: it resolves the session (owned-by
check, same pattern as `app/sessions/routes.py`), wires a `ToolRegistry` +
`ToolContext`, and streams whatever `run_turn` yields as SSE frames. Stage 6
will inline `attachments` into context; for now the field is accepted but
ignored so the frontend contract doesn't need to change later.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.auth.deps import CurrentUser, DbSession
from app.chat.client import ModelClient, get_model_client
from app.chat.loop import run_turn
from app.errors import ApiError
from app.kb.routes import get_embedder_dep, get_qdrant
from app.models import ChatSession
from app.rag.embedder import Embedder
from app.sse import sse
from app.tools.builtin.search_kb import SearchKnowledgeBase
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry

router = APIRouter()

QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder_dep)]
ModelClientDep = Annotated[ModelClient, Depends(get_model_client)]


class ChatRequest(BaseModel):
    message: str
    attachments: list[Any] | None = None  # accepted, ignored until Stage 6


def _owned(db: DbSession, user_id: str, session_id: str) -> ChatSession:
    """Fetch a session or 404 — never leak another user's session (§5)."""
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise ApiError(404, "not_found", "Session not found")
    return s


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SearchKnowledgeBase())
    return registry


@router.post("/{session_id}/chat")
def chat(
    session_id: str,
    body: ChatRequest,
    user: CurrentUser,
    db: DbSession,
    client: QdrantDep,
    embedder: EmbedderDep,
    model: ModelClientDep,
) -> StreamingResponse:
    session = _owned(db, user.id, session_id)

    registry = _build_registry()
    ctx = ToolContext(
        user_id=user.id,
        session_id=session_id,
        db=db,
        client=client,
        embedder=embedder,
    )

    async def gen():
        async for event in run_turn(
            db=db, session=session, message=body.message,
            registry=registry, model=model, ctx=ctx,
        ):
            yield sse(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
