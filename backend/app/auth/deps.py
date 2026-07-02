"""Auth dependencies — extract and validate the bearer access token (§4)."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.db import get_db
from app.errors import ApiError
from app.models import User


def _unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "Unauthorized")


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized()
    token = authorization.split(" ", 1)[1].strip()
    user_id = decode_access_token(token)
    if not user_id:
        raise _unauthorized()
    user = db.get(User, user_id)
    if not user:
        raise _unauthorized()
    if user.disabled:
        raise ApiError(403, "account_disabled", "Account is disabled")
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise ApiError(403, "forbidden", "Admin access required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]
AdminUser = Annotated[User, Depends(require_admin)]
