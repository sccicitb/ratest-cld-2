"""KB file repo (§8.2, §8.3): list/filter, create, ownership lookup, mutate."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import KBFile


def list_kb(
    db: Session,
    user_id: str,
    *,
    search: str | None = None,
    status: str | None = None,
    tag: str | None = None,
) -> list[KBFile]:
    """List a user's KB-scope files, newest first, with AND-combined filters."""
    query = db.query(KBFile).filter(KBFile.user_id == user_id, KBFile.scope == "kb")
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
