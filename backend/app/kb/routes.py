"""Knowledge-base endpoints (§8.2, §8.3): list/filter, upload (SSE), reindex, tags, delete."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.auth.deps import CurrentUser, DbSession
from app.db import get_session_factory
from app.errors import ApiError
from app.groups.service import group_ids_for
from app.kb import repo
from app.models import Group, User
from app.rag.embedder import Embedder, get_embedder
from app.rag.extract import SUPPORTED_KB_TYPES
from app.rag.ingest import ingest
from app.rag.ingest_jobs import IngestJobRegistry
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


def get_ingest_jobs(request: Request) -> IngestJobRegistry:
    return request.app.state.ingest_jobs


QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder_dep)]
SessionFactoryDep = Annotated[object, Depends(get_session_factory)]
IngestJobsDep = Annotated[IngestJobRegistry, Depends(get_ingest_jobs)]


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
    return repo.list_accessible(
        db,
        caller_group_ids=group_ids_for(user),
        is_admin=user.is_admin,
        search=search,
        status=status,
        tag=tag,
    )


def _resolve_kb_filing(
    db: DbSession,
    user: User,
    *,
    group_id: str | None,
    is_public: bool,
    tags: list[str] | None,
) -> tuple[str | None, bool, list[str]]:
    """Apply the M3 role-based filing rules → (group_id, is_public, tags).

    Regular user: files into ONE of their OWN groups (single → auto; multi →
    must name a valid own group; none → 403). Inherits that group's default_tags
    (client tags/public ignored). Admin: any group + public + free tags.
    """
    if user.is_admin:
        if group_id is not None and db.get(Group, group_id) is None:
            raise ApiError(400, "invalid_group", "Group not found")
        # Guard the unrecoverable-invisible case: a doc with no group AND not
        # public can never be surfaced by the scope filter, and there's no API
        # to fix it after upload. Make the admin choose a group or public.
        if group_id is None and not is_public:
            raise ApiError(
                400, "group_or_public_required",
                "Choose a group or mark the file public",
            )
        return group_id, is_public, tags or []

    my_group_ids = group_ids_for(user)
    if not my_group_ids:
        raise ApiError(403, "no_group", "Ask an admin to add you to a group before uploading")
    if group_id is None:
        if len(my_group_ids) > 1:
            raise ApiError(400, "group_required", "Choose which of your groups to file under")
        chosen = my_group_ids[0]
    else:
        if group_id not in my_group_ids:
            raise ApiError(403, "forbidden_group", "You can only file into your own groups")
        chosen = group_id
    group = db.get(Group, chosen)
    if group is None:  # deleted between membership resolution and now
        raise ApiError(400, "invalid_group", "Group not found")
    return chosen, False, list(group.default_tags or [])


@router.post("/upload")
async def upload_file(
    user: CurrentUser,
    db: DbSession,
    ingest_jobs: IngestJobsDep,
    file: UploadFile,
    group_id: Annotated[str | None, Form()] = None,
    is_public: Annotated[bool, Form()] = False,
    tags: Annotated[str | None, Form()] = None,
) -> StreamingResponse:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_KB_TYPES:
        raise ApiError(415, "unsupported_media_type", f"Unsupported file type: {ext!r}")

    parsed_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    resolved_group, resolved_public, resolved_tags = _resolve_kb_filing(
        db, user, group_id=group_id, is_public=is_public, tags=parsed_tags
    )

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
        group_id=resolved_group,
        is_public=resolved_public,
        tags=resolved_tags,
    )
    file_id = kb_file.id
    ingest_jobs.spawn(file_id)

    async def _stream():
        async for event in ingest_jobs.observe(file_id):
            yield sse(event)
        # Task finished (or was never observed). Re-read the file for its terminal state.
        db.refresh(kb_file)
        if kb_file.status == "error":
            yield sse({"type": "error", "message": "Ingestion failed"})
            return
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
    file = repo.get_manageable(db, user_id=user.id, is_admin=user.is_admin, file_id=file_id)
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
    file = repo.get_manageable(db, user_id=user.id, is_admin=user.is_admin, file_id=file_id)
    return repo.update_tags(db, file, body.tags)


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: str, user: CurrentUser, db: DbSession, client: QdrantDep) -> None:
    file = repo.get_manageable(db, user_id=user.id, is_admin=user.is_admin, file_id=file_id)
    delete_by_file(client, file_id)
    delete_blob(file.storage_key)
    repo.delete(db, file)
