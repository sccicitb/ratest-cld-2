"""Auth dependencies — extract and validate the bearer access token (§4)."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.db import get_db
from app.models import User


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail={"message": "Unauthorized", "code": "unauthorized"})


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
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]
