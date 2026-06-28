"""Chat HTTP route (§7) — wraps `run_turn` in an SSE response.

The route itself does no chat-loop logic: it resolves the session (owned-by
check, same pattern as `app/sessions/routes.py`), wires a `ToolRegistry` +
`ToolContext`, and streams whatever `run_turn` yields as SSE frames.

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
from app.chat.loop import run_turn
from app.errors import ApiError
from app.kb.routes import get_embedder_dep, get_qdrant
from app.models import Attachment, ChatSession
from app.rag.embedder import Embedder
from app.rag.extract import extract_text
from app.sse import sse
from app.tools.builtin.execute_code import ExecuteCode
from app.tools.builtin.search_kb import SearchKnowledgeBase
from app.tools.context import ToolContext
from app.tools.registry import Tool, ToolRegistry

router = APIRouter()

QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder_dep)]
ModelClientDep = Annotated[ModelClient, Depends(get_model_client)]


class ChatRequest(BaseModel):
    message: str
    attachments: list[dict[str, Any]] | None = None


def _owned(db: DbSession, user_id: str, session_id: str) -> ChatSession:
    """Fetch a session or 404 — never leak another user's session (§5)."""
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise ApiError(404, "not_found", "Session not found")
    return s


def get_mcp_tools(request: Request) -> list[Tool]:
    """Return MCP tools stashed on app.state by the lifespan — empty list if none."""
    return getattr(request.app.state, "mcp_tools", [])


def _build_registry(mcp_tools: list[Tool]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SearchKnowledgeBase())
    registry.register(ExecuteCode())
    for t in mcp_tools:
        registry.register(t)
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
    mcp_tools: Annotated[list[Tool], Depends(get_mcp_tools)],
) -> StreamingResponse:
    session = _owned(db, user.id, session_id)

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

    registry = _build_registry(mcp_tools)
    ctx = ToolContext(
        user_id=user.id,
        session_id=session_id,
        db=db,
        client=client,
        embedder=embedder,
    )

    async def gen():
        async for event in run_turn(
            db=db,
            session=session,
            message=body.message,
            model_content=model_content,
            attachment_ids=attachment_ids,
            registry=registry,
            model=model,
            ctx=ctx,
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
