"""Knowledge-base endpoints (§8.2, §8.3): list/filter, upload (SSE), reindex, tags, delete."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.auth.deps import CurrentUser, DbSession
from app.db import get_session_factory
from app.errors import ApiError
from app.kb import repo
from app.rag.embedder import Embedder, get_embedder
from app.rag.extract import SUPPORTED_KB_TYPES
from app.rag.ingest import ingest
from app.rag.vectors import delete_by_file, get_client
from app.schemas import KnowledgeBaseFileOut
from app.sse import sse
from app.storage import delete_blob, save_upload

logger = logging.getLogger(__name__)

router = APIRouter()


# --- DI seams (overridable in tests) ----------------------------------------


def get_qdrant() -> QdrantClient:
    return get_client()


def get_embedder_dep() -> Embedder:
    return get_embedder()


QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder_dep)]
SessionFactoryDep = Annotated[object, Depends(get_session_factory)]


class UpdateTagsRequest(BaseModel):
    tags: list[str]


class _SyncUpload:
    """Adapts FastAPI's `UploadFile` to `storage._UploadLike` (sync `.read()`)."""

    def __init__(self, filename: str | None, fileobj) -> None:
        self.filename = filename
        self._fileobj = fileobj

    def read(self) -> bytes:
        return self._fileobj.read()


# --- Routes ------------------------------------------------------------------


@router.get("", response_model=list[KnowledgeBaseFileOut])
def list_files(
    user: CurrentUser,
    db: DbSession,
    search: str | None = None,
    status: str | None = None,
    tag: str | None = None,
) -> list:
    return repo.list_kb(db, user.id, search=search, status=status, tag=tag)


@router.post("/upload")
async def upload_file(
    user: CurrentUser,
    db: DbSession,
    client: QdrantDep,
    embedder: EmbedderDep,
    file: UploadFile,
) -> StreamingResponse:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_KB_TYPES:
        raise ApiError(415, "unsupported_media_type", f"Unsupported file type: {ext!r}")

    # `UploadFile.read()` is async; `save_upload` expects the sync `_UploadLike`
    # protocol (plain `.read()`), so hand it the underlying spooled file object
    # paired with the original filename.
    file.file.seek(0)
    storage_key, size = save_upload(_SyncUpload(file.filename, file.file))
    kb_file = repo.create(
        db,
        user_id=user.id,
        name=file.filename or storage_key,
        size=size,
        storage_key=storage_key,
        scope="kb",
    )
    file_id = kb_file.id

    async def _stream():
        try:
            async for event in ingest(db, file_id, client=client, embedder=embedder):
                yield sse(event)
        except Exception as exc:
            yield sse({"type": "error", "message": str(exc)})
            return

        db.refresh(kb_file)
        out = KnowledgeBaseFileOut.model_validate(kb_file)
        yield sse({"type": "file_resolved", "file": out.model_dump(mode="json", by_alias=True)})
        yield sse({"type": "done"})

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/{file_id}/reindex", response_model=KnowledgeBaseFileOut)
def reindex_file(
    file_id: str,
    user: CurrentUser,
    db: DbSession,
    client: QdrantDep,
    embedder: EmbedderDep,
    background_tasks: BackgroundTasks,
    session_factory: SessionFactoryDep,
) -> object:
    file = repo.get_owned(db, user.id, file_id)
    file = repo.set_status(db, file, status="indexing", chunk_count=0)

    def _run() -> None:
        import asyncio

        async def _consume():
            bg_db = session_factory()
            try:
                async for _ in ingest(bg_db, file_id, client=client, embedder=embedder):
                    pass
            finally:
                bg_db.close()

        try:
            asyncio.run(_consume())
        except Exception:
            logger.exception("reindex ingest failed for %s", file_id)

    background_tasks.add_task(_run)
    return file


@router.patch("/{file_id}/tags", response_model=KnowledgeBaseFileOut)
def update_tags(file_id: str, body: UpdateTagsRequest, user: CurrentUser, db: DbSession) -> object:
    file = repo.get_owned(db, user.id, file_id)
    return repo.update_tags(db, file, body.tags)


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: str, user: CurrentUser, db: DbSession, client: QdrantDep) -> None:
    file = repo.get_owned(db, user.id, file_id)
    delete_by_file(client, file_id)
    delete_blob(file.storage_key)
    repo.delete(db, file)
