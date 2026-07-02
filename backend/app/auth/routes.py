"""Auth endpoints (§4): login, refresh (rotation), me, logout, change-password."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Response

from app.auth import security
from app.auth.deps import CurrentUser, DbSession
from app.config import settings
from app.errors import ApiError
from app.models import RefreshToken, User
from app.schemas import AuthResponse, ChangePasswordRequest, LoginRequest, UserOut

router = APIRouter()

_COOKIE = "refresh_token"
_COOKIE_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=_COOKIE,
        value=raw_token,
        httponly=True,
        secure=settings.cookie_secure,  # works on localhost; required for split-origin
        samesite="lax",  # same-origin (reverse proxy / Vite proxy) — §2
        path=_COOKIE_PATH,
        max_age=settings.refresh_token_ttl_days * 86400,
    )


def _issue_refresh(db: DbSession, response: Response, user_id: str) -> None:
    raw = security.new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=security.hash_refresh_token(raw),
            expires_at=security.refresh_expiry(),
        )
    )
    db.commit()
    _set_refresh_cookie(response, raw)


def _auth_response(user: User, access_token: str) -> AuthResponse:
    return AuthResponse(access_token=access_token, user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthResponse)
def login(body: LoginRequest, response: Response, db: DbSession) -> AuthResponse:
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not security.verify_password(body.password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "Invalid credentials")
    if user.disabled:
        raise ApiError(403, "account_disabled", "Account is disabled")
    _issue_refresh(db, response, user.id)
    return _auth_response(user, security.create_access_token(user.id))


@router.post("/refresh", response_model=AuthResponse)
def refresh(
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
) -> AuthResponse:
    invalid = ApiError(401, "invalid_refresh", "Invalid refresh")
    if not refresh_token:
        raise invalid

    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == security.hash_refresh_token(refresh_token))
        .first()
    )
    if not row:
        raise invalid

    # Reuse of an already-revoked token ⇒ treat as compromise: revoke the chain.
    if row.revoked:
        db.query(RefreshToken).filter(RefreshToken.user_id == row.user_id).update(
            {RefreshToken.revoked: True}
        )
        db.commit()
        raise invalid

    # SQLite returns naive datetimes — coerce to UTC-aware before comparing.
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid

    user = db.get(User, row.user_id)
    if not user:
        raise invalid

    # Rotate: revoke the presented token, issue a fresh pair.
    row.revoked = True
    db.commit()
    _issue_refresh(db, response, user.id)
    return _auth_response(user, security.create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/logout")
def logout(
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
) -> dict:
    if refresh_token:
        db.query(RefreshToken).filter(
            RefreshToken.token_hash == security.hash_refresh_token(refresh_token)
        ).update({RefreshToken.revoked: True})
        db.commit()
    response.delete_cookie(_COOKIE, path=_COOKIE_PATH)
    return {}


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: CurrentUser, db: DbSession) -> dict:
    if not security.verify_password(body.old_password, user.password_hash):
        raise ApiError(400, "invalid_password", "Current password is incorrect")
    if len(body.new_password) < 8:
        raise ApiError(400, "password_too_short", "New password must be at least 8 characters")
    user.password_hash = security.hash_password(body.new_password)
    db.commit()
    return {}
