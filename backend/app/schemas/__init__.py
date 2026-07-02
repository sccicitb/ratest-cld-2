"""Request/response DTOs.

Fields are snake_case (matching the ORM attributes for `from_attributes`), and
serialized to **camelCase** via the alias generator — that's the wire contract
the frontend expects (§2). FastAPI emits response models by alias by default.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    @field_serializer("*", when_used="json", check_fields=False)
    def _utc_datetimes(self, v):  # noqa: ANN001
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return v


# --- Auth (§4) ---
class LoginRequest(CamelModel):
    email: str
    password: str


class UserOut(CamelModel):
    id: str
    email: str
    display_name: str
    avatar_url: str | None = None
    is_admin: bool = False
    disabled: bool = False
    created_at: datetime | None = None


class AuthResponse(CamelModel):
    access_token: str
    user: UserOut


# --- Sessions (§5) ---
class SessionOut(CamelModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class RenameSessionRequest(CamelModel):
    title: str


# --- Messages / attachments (§3) ---
class AttachmentOut(CamelModel):
    id: str
    file_name: str
    file_type: str
    file_size: int
    url: str
    thumbnail_url: str | None = None
    ingested: bool = False


class MessageOut(CamelModel):
    id: str
    session_id: str
    role: str
    content: str
    attachments: list[AttachmentOut] = []
    created_at: datetime


# --- Knowledge base (§8) ---
class KnowledgeBaseFileOut(CamelModel):
    id: str
    name: str
    size: int
    upload_date: datetime
    chunk_count: int
    status: str
    tags: list[str] = []
    scope: str = "kb"


# --- Artifacts (§v1.1 Stage A1) ---
class ArtifactOut(CamelModel):
    id: str
    title: str
    latest_version: int
    created_at: datetime


# --- Admin (§M1) ---
class CreateUserRequest(CamelModel):
    email: str
    display_name: str
    password: str
    is_admin: bool = False


class PatchUserRequest(CamelModel):
    disabled: bool | None = None
    is_admin: bool | None = None
    display_name: str | None = None


class ResetPasswordResponse(CamelModel):
    temp_password: str


class ChangePasswordRequest(CamelModel):
    old_password: str
    new_password: str


__all__ = [
    "CamelModel",
    "LoginRequest",
    "UserOut",
    "AuthResponse",
    "SessionOut",
    "RenameSessionRequest",
    "AttachmentOut",
    "MessageOut",
    "KnowledgeBaseFileOut",
    "ArtifactOut",
    "CreateUserRequest",
    "PatchUserRequest",
    "ResetPasswordResponse",
    "ChangePasswordRequest",
]
