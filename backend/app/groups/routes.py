"""Non-admin group endpoints — available to any authenticated user."""
from __future__ import annotations

from fastapi import APIRouter

from app.auth.deps import CurrentUser, DbSession
from app.schemas import GroupOut

router = APIRouter()


@router.get("/mine", response_model=list[GroupOut])
def my_groups(user: CurrentUser, db: DbSession) -> list[GroupOut]:
    """Return the groups the caller belongs to (0, 1, or many)."""
    return [GroupOut.model_validate(g) for g in user.groups]
