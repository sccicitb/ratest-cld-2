"""Admin group-management endpoints (§M2). All require AdminUser."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.exc import IntegrityError

from app.auth.deps import AdminUser, DbSession
from app.errors import ApiError
from app.models import Group, User
from app.schemas import (
    CreateGroupRequest,
    GroupDetailOut,
    GroupMembersRequest,
    GroupOut,
    UpdateGroupRequest,
)

router = APIRouter()


@router.post("/groups", response_model=GroupOut, status_code=201)
def create_group(body: CreateGroupRequest, _admin: AdminUser, db: DbSession) -> GroupOut:
    group = Group(name=body.name, default_tags=body.default_tags)
    db.add(group)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "name_taken", "A group with that name already exists")
    db.refresh(group)
    return GroupOut.model_validate(group)


@router.get("/groups", response_model=list[GroupOut])
def list_groups(_admin: AdminUser, db: DbSession) -> list[GroupOut]:
    groups = db.query(Group).order_by(Group.created_at).all()
    return [GroupOut.model_validate(g) for g in groups]


@router.get("/groups/{group_id}", response_model=GroupDetailOut)
def get_group(group_id: str, _admin: AdminUser, db: DbSession) -> GroupDetailOut:
    group = db.get(Group, group_id)
    if not group:
        raise ApiError(404, "not_found", "Group not found")
    return GroupDetailOut.model_validate(group)


@router.patch("/groups/{group_id}", response_model=GroupOut)
def patch_group(
    group_id: str, body: UpdateGroupRequest, _admin: AdminUser, db: DbSession
) -> GroupOut:
    group = db.get(Group, group_id)
    if not group:
        raise ApiError(404, "not_found", "Group not found")
    if body.name is not None:
        group.name = body.name
    if body.default_tags is not None:
        group.default_tags = body.default_tags
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "name_taken", "A group with that name already exists")
    db.refresh(group)
    return GroupOut.model_validate(group)


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: str, _admin: AdminUser, db: DbSession) -> None:
    group = db.get(Group, group_id)
    if not group:
        raise ApiError(404, "not_found", "Group not found")
    db.delete(group)
    db.commit()


@router.put("/groups/{group_id}/members", response_model=GroupDetailOut)
def set_members(
    group_id: str, body: GroupMembersRequest, _admin: AdminUser, db: DbSession
) -> GroupDetailOut:
    group = db.get(Group, group_id)
    if not group:
        raise ApiError(404, "not_found", "Group not found")

    members: list[User] = []
    for uid in body.user_ids:
        user = db.get(User, uid)
        if not user:
            raise ApiError(400, "user_not_found", f"User {uid!r} not found")
        members.append(user)

    group.members = members
    db.commit()
    db.refresh(group)
    return GroupDetailOut.model_validate(group)
