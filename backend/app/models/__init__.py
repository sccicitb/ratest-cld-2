"""SQLAlchemy ORM models — the app data store (§9.1).

Chunk vectors live in Qdrant (§9.2), not here. The ORM `ChatSession` maps the
`sessions` table (named to avoid colliding with `sqlalchemy.orm.Session`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RefreshToken(Base):
    """Hashed, revocable refresh tokens (§4). Never store the raw token."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, default="New Chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # Nullable: §6.1 uploads create the row before it's bound to a message on send.
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    file_name: Mapped[str] = mapped_column(String)
    file_type: Mapped[str] = mapped_column(String)
    file_size: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String)
    thumbnail_url: Mapped[str | None] = mapped_column(String, nullable=True)
    ingested: Mapped[bool] = mapped_column(Boolean, default=False)

    message: Mapped["Message | None"] = relationship(back_populates="attachments")


class KBFile(Base):
    """Knowledge-base / session file metadata. Vectors live in Qdrant (§9.2)."""

    __tablename__ = "kb_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String, default="kb")  # kb | session
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(Integer)
    upload_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="indexing")  # indexing|ready|error
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # JSON, not native array
    storage_key: Mapped[str] = mapped_column(String)


# ---------------------------------------------------------------------------
# v1.1 Artifacts pillar (§Stage A1): model-authored HTML report artifacts
# ---------------------------------------------------------------------------


class Artifact(Base):
    """A versioned HTML report artifact created by the model via `create_artifact`.

    Owned by a session (cascade-deletes with it). Each Artifact has one or more
    ArtifactVersion rows whose storage_key points to a blob in blob_dir.
    """

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String)
    latest_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    versions: Mapped[list["ArtifactVersion"]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )


class ArtifactVersion(Base):
    """One snapshot of an Artifact's HTML content.

    Sequentially numbered per artifact (1, 2, ...). Optionally linked to
    the assistant message that produced it.
    """

    __tablename__ = "artifact_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String)
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    artifact: Mapped["Artifact"] = relationship(back_populates="versions")


__all__ = [
    "User",
    "RefreshToken",
    "ChatSession",
    "Message",
    "Attachment",
    "KBFile",
    "Artifact",
    "ArtifactVersion",
]
