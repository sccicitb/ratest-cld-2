"""Sessions & messages endpoints (§5). All scoped to the authenticated user."""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from qdrant_client import QdrantClient

from app.auth.deps import CurrentUser, DbSession
from app.chat.routes import ChatTurnsDep
from app.config import settings
from app.errors import ApiError
from app.kb.routes import get_qdrant
from app.models import Artifact, ArtifactVersion, ChatSession, KBFile, Message
from app.rag.vectors import delete_by_session
from app.schemas import MessageOut, RenameSessionRequest, SessionOut
from app.storage import delete_blob

log = logging.getLogger(__name__)

router = APIRouter()

QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]


def _owned(db: DbSession, user_id: str, session_id: str) -> ChatSession:
    """Fetch a session or 404 — never leak another user's session (§5)."""
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise ApiError(404, "not_found", "Session not found")
    return s


@router.get("", response_model=list[SessionOut])
def list_sessions(user: CurrentUser, db: DbSession, chat_turns: ChatTurnsDep) -> list[ChatSession]:
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    active = chat_turns.active_session_ids()
    for s in sessions:
        s.active_turn = s.id in active
    return sessions


@router.post("", response_model=SessionOut, status_code=201)
def create_session(user: CurrentUser, db: DbSession) -> ChatSession:
    s = ChatSession(user_id=user.id, title="New Chat")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str, user: CurrentUser, db: DbSession) -> ChatSession:
    return _owned(db, user.id, session_id)


@router.patch("/{session_id}", response_model=SessionOut)
def rename_session(
    session_id: str, body: RenameSessionRequest, user: CurrentUser, db: DbSession
) -> ChatSession:
    s = _owned(db, user.id, session_id)
    s.title = body.title
    db.commit()
    db.refresh(s)
    return s


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, user: CurrentUser, db: DbSession, client: QdrantDep) -> None:
    s = _owned(db, user.id, session_id)
    # §8.2 ordering: purge Qdrant points first, then cascade rows.
    delete_by_session(client, session_id)
    # Clean up blobs for session-scoped KB files before the rows cascade.
    for kbf in (
        db.query(KBFile).filter(KBFile.session_id == session_id, KBFile.scope == "session").all()
    ):
        try:
            delete_blob(kbf.storage_key)
        except Exception:
            pass
    # Clean up artifact HTML blobs before the rows cascade.
    for av in (
        db.query(ArtifactVersion)
        .join(Artifact)
        .filter(Artifact.session_id == session_id)
        .all()
    ):
        try:
            delete_blob(av.storage_key)
        except Exception:
            pass
    # §13: best-effort sandbox container teardown — must NOT block deletion.
    try:
        with httpx.Client(timeout=5) as hc:
            hc.delete(f"{settings.code_exec_url}/sessions/{session_id}")
    except Exception as exc:
        log.warning("sandbox teardown failed for session %s: %s", session_id, exc)

    db.delete(s)
    db.commit()


@router.get("/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: str, user: CurrentUser, db: DbSession) -> list[Message]:
    _owned(db, user.id, session_id)
    return (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
        .all()
    )
