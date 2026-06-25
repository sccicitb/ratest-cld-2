"""Sessions & messages endpoints (§5). All scoped to the authenticated user."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.auth.deps import CurrentUser, DbSession
from app.models import ChatSession, Message
from app.schemas import MessageOut, RenameSessionRequest, SessionOut

router = APIRouter()


def _owned(db: DbSession, user_id: str, session_id: str) -> ChatSession:
    """Fetch a session or 404 — never leak another user's session (§5)."""
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise HTTPException(404, {"message": "Session not found", "code": "not_found"})
    return s


@router.get("", response_model=list[SessionOut])
def list_sessions(user: CurrentUser, db: DbSession) -> list[ChatSession]:
    return (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )


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
def delete_session(session_id: str, user: CurrentUser, db: DbSession) -> None:
    s = _owned(db, user.id, session_id)
    # NOTE: cascades messages/attachments via FK. Session-scoped Qdrant points
    # + kb_files are removed here too once retrieval lands (§8.2).
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
