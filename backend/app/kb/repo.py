"""KB file repo (§8.2, §8.3): list/filter, create, ownership lookup, mutate."""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import KBFile


def list_accessible(
    db: Session,
    *,
    caller_group_ids: list[str],
    is_admin: bool,
    search: str | None = None,
    status: str | None = None,
    tag: str | None = None,
) -> list[KBFile]:
    """List KB-scope files the caller may see, newest first (§8/M3).

    Admin sees all KB files. A regular user sees `is_public OR group_id ∈ their
    groups`. Session files never appear on the KB page.
    """
    query = db.query(KBFile).filter(KBFile.scope == "kb")
    if not is_admin:
        conds = [KBFile.is_public.is_(True)]
        if caller_group_ids:
            conds.append(KBFile.group_id.in_(caller_group_ids))
        query = query.filter(or_(*conds))
    if search:
        query = query.filter(KBFile.name.ilike(f"%{search}%"))
    if status:
        query = query.filter(KBFile.status == status)
    files = query.order_by(KBFile.upload_date.desc()).all()
    if tag:
        files = [f for f in files if tag in (f.tags or [])]
    return files


def create(
    db: Session,
    *,
    user_id: str,
    name: str,
    size: int,
    storage_key: str,
    scope: str = "kb",
    session_id: str | None = None,
    tags: list[str] | None = None,
    group_id: str | None = None,
    is_public: bool = False,
) -> KBFile:
    file = KBFile(
        user_id=user_id,
        scope=scope,
        session_id=session_id,
        name=name,
        size=size,
        storage_key=storage_key,
        status="indexing",
        chunk_count=0,
        tags=tags or [],
        group_id=group_id,
        is_public=is_public,
    )
    db.add(file)
    db.commit()
    db.refresh(file)
    return file


def get_owned(db: Session, user_id: str, file_id: str) -> KBFile:
    file = db.get(KBFile, file_id)
    if not file or file.user_id != user_id:
        raise ApiError(404, "not_found", "File not found")
    return file


def get_manageable(db: Session, *, user_id: str, is_admin: bool, file_id: str) -> KBFile:
    """A file the caller may delete/reindex/retag: their own upload, or ANY if admin."""
    file = db.get(KBFile, file_id)
    if not file or (not is_admin and file.user_id != user_id):
        raise ApiError(404, "not_found", "File not found")
    return file


def set_status(db: Session, file: KBFile, *, status: str, chunk_count: int | None = None) -> KBFile:
    file.status = status
    if chunk_count is not None:
        file.chunk_count = chunk_count
    db.commit()
    db.refresh(file)
    return file


def update_tags(db: Session, file: KBFile, tags: list[str]) -> KBFile:
    deduped = sorted({t.lower() for t in tags})
    file.tags = deduped
    db.commit()
    db.refresh(file)
    return file


def delete(db: Session, file: KBFile) -> None:
    db.delete(file)
    db.commit()
