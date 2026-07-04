"""Non-admin group endpoints — available to any authenticated user."""
from __future__ import annotations

from fastapi import APIRouter

from app.auth.deps import CurrentUser
from app.schemas import GroupOut

router = APIRouter()


@router.get("/mine", response_model=list[GroupOut])
def my_groups(user: CurrentUser) -> list[GroupOut]:
    """Return the groups the caller belongs to (0, 1, or many).

    `user.groups` is already loaded on the request's session (bound by
    get_current_user), so no extra DB dependency is needed.
    """
    return [GroupOut.model_validate(g) for g in user.groups]
