"""Session attachments + session files endpoints (§6).

POST /sessions/{id}/attachments  — multipart→SSE upload (inline / ingest).
GET  /sessions/{id}/files        — list session-scoped KB files.
POST /sessions/{id}/files/{fid}/promote — flip scope→kb + update Qdrant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import StreamingResponse
from qdrant_client import QdrantClient

from app.auth.deps import CurrentUser, DbSession
from app.errors import ApiError
from app.kb import repo as kb_repo
from app.kb.routes import get_embedder_dep, get_qdrant
from app.models import Attachment, ChatSession, KBFile
from app.rag.embedder import Embedder
from app.rag.extract import extract_text
from app.rag.ingest import ingest
from app.rag.tokens import route_by_tokens
from app.rag.vectors import update_file_payload
from app.schemas import AttachmentOut, KnowledgeBaseFileOut
from app.sse import sse
from app.storage import save_upload

router = APIRouter()

QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder_dep)]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _SyncUpload:
    """Adapt FastAPI `UploadFile` to the sync `_UploadLike` protocol that
    `storage.save_upload` expects."""

    def __init__(self, filename: str | None, fileobj) -> None:
        self.filename = filename
        self._fileobj = fileobj

    def read(self) -> bytes:
        return self._fileobj.read()


def _owned(db: DbSession, user_id: str, session_id: str) -> ChatSession:
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise ApiError(404, "not_found", "Session not found")
    return s


# ---------------------------------------------------------------------------
# POST /sessions/{id}/attachments  (multipart → SSE)
# ---------------------------------------------------------------------------


@router.post("/{session_id}/attachments")
async def upload_attachments(
    session_id: str,
    user: CurrentUser,
    db: DbSession,
    client: QdrantDep,
    embedder: EmbedderDep,
    files: list[UploadFile],
) -> StreamingResponse:
    _owned(db, user.id, session_id)

    async def _stream():
        for file in files:
            try:
                # Persist the raw blob.
                file.file.seek(0)
                storage_key, size = save_upload(_SyncUpload(file.filename, file.file))

                # Extract text and decide route.
                text = extract_text(storage_key, file.filename or storage_key)
                decision = route_by_tokens(text)

                if decision == "inline":
                    att = Attachment(
                        message_id=None,
                        file_name=file.filename or storage_key,
                        file_type=file.content_type or Path(file.filename or "").suffix,
                        file_size=size,
                        url=storage_key,
                        ingested=False,
                    )
                    db.add(att)
                    db.commit()
                    db.refresh(att)
                    out = AttachmentOut.model_validate(att)
                    yield sse(
                        {
                            "type": "attachment_resolved",
                            "attachment": out.model_dump(mode="json", by_alias=True),
                        }
                    )
                else:
                    # Ingest path: create Attachment + KBFile, then run the
                    # async ingest pipeline, streaming chunk_progress events.
                    att = Attachment(
                        message_id=None,
                        file_name=file.filename or storage_key,
                        file_type=file.content_type or Path(file.filename or "").suffix,
                        file_size=size,
                        url=storage_key,
                        ingested=True,
                    )
                    db.add(att)
                    kb_file = kb_repo.create(
                        db,
                        user_id=user.id,
                        name=file.filename or storage_key,
                        size=size,
                        storage_key=storage_key,
                        scope="session",
                        session_id=session_id,
                    )
                    file_id = kb_file.id
                    db.refresh(att)

                    try:
                        async for event in ingest(db, file_id, client=client, embedder=embedder):
                            yield sse(event)
                    except Exception as exc:
                        yield sse({"type": "error", "message": str(exc)})
                        # Continue to next file — don't abort the whole stream
                        continue

                    db.refresh(kb_file)
                    db.refresh(att)
                    out = AttachmentOut.model_validate(att)
                    yield sse(
                        {
                            "type": "attachment_resolved",
                            "attachment": out.model_dump(mode="json", by_alias=True),
                        }
                    )
            except Exception as exc:
                yield sse({"type": "error", "message": str(exc)})
                # Continue with remaining files.

        yield sse({"type": "done"})

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# GET  /sessions/{id}/files
# POST /sessions/{id}/files/{file_id}/promote
# ---------------------------------------------------------------------------


@router.get("/{session_id}/files", response_model=list[KnowledgeBaseFileOut])
def list_session_files(
    session_id: str,
    user: CurrentUser,
    db: DbSession,
) -> list[KBFile]:
    _owned(db, user.id, session_id)
    return (
        db.query(KBFile)
        .filter(
            KBFile.user_id == user.id,
            KBFile.session_id == session_id,
            KBFile.scope == "session",
        )
        .order_by(KBFile.upload_date.desc())
        .all()
    )


@router.post("/{session_id}/files/{file_id}/promote", response_model=KnowledgeBaseFileOut)
def promote_session_file(
    session_id: str,
    file_id: str,
    user: CurrentUser,
    db: DbSession,
    client: QdrantDep,
) -> KBFile:
    _owned(db, user.id, session_id)
    kb_file = kb_repo.get_owned(db, user.id, file_id)
    if kb_file.scope != "session" or kb_file.session_id != session_id:
        raise ApiError(404, "not_found", "Session file not found")

    # Flip scope in the DB row.
    kb_file.scope = "kb"
    kb_file.session_id = None
    db.commit()
    db.refresh(kb_file)

    # Patch the denormalized Qdrant payload so search filters pick it up.
    update_file_payload(client, file_id, {"scope": "kb", "session_id": None})

    return kb_file
