"""Admin user-management endpoints (§M1). All require AdminUser."""
from __future__ import annotations

import secrets

from fastapi import APIRouter

from app.auth.deps import AdminUser, DbSession
from app.auth.security import hash_password
from app.errors import ApiError
from app.models import User
from app.schemas import CreateUserRequest, PatchUserRequest, ResetPasswordResponse, UserOut

router = APIRouter()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: CreateUserRequest, _admin: AdminUser, db: DbSession) -> UserOut:
    if len(body.password) < 8:
        raise ApiError(400, "invalid_password", "Password must be at least 8 characters")
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise ApiError(409, "email_taken", "A user with that email already exists")
    user = User(
        email=body.email.lower(),
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/users", response_model=list[UserOut])
def list_users(_admin: AdminUser, db: DbSession) -> list[UserOut]:
    users = db.query(User).order_by(User.created_at).all()
    return [UserOut.model_validate(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: str, body: PatchUserRequest, admin: AdminUser, db: DbSession
) -> UserOut:
    user = db.get(User, user_id)
    if not user:
        raise ApiError(404, "not_found", "User not found")

    # Self-lockout guard: admin can't disable or demote themselves
    if user.id == admin.id:
        if body.disabled is True:
            raise ApiError(403, "self_lockout", "You cannot disable your own account")
        if body.is_admin is False:
            raise ApiError(403, "self_lockout", "You cannot remove your own admin privileges")

    if body.disabled is not None:
        user.disabled = body.disabled
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.display_name is not None:
        user.display_name = body.display_name

    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def reset_password(user_id: str, _admin: AdminUser, db: DbSession) -> ResetPasswordResponse:
    user = db.get(User, user_id)
    if not user:
        raise ApiError(404, "not_found", "User not found")
    temp_password = secrets.token_urlsafe(12)
    user.password_hash = hash_password(temp_password)
    db.commit()
    return ResetPasswordResponse(temp_password=temp_password)
