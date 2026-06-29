"""Artifact list + raw serve endpoints (§v1.1 Stage A1).

- ``GET  /sessions/{sid}/artifacts`` — list the session's artifacts.
- ``GET  /sessions/{sid}/artifacts/{aid}/raw[?version=N]`` — serve the HTML blob.

All endpoints are auth'd and ownership-checked; cross-user access returns 404.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.auth.deps import CurrentUser, DbSession
from app.errors import ApiError
from app.models import Artifact, ArtifactVersion, ChatSession
from app.schemas import ArtifactOut
from app.storage import open_blob

router = APIRouter()


def _owned_session(db: DbSession, user_id: str, session_id: str) -> ChatSession:
    """Fetch the session or 404 — never leak another user's session."""
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != user_id:
        raise ApiError(404, "not_found", "Session not found")
    return s


# ---------------------------------------------------------------------------
# GET /sessions/{sid}/artifacts
# ---------------------------------------------------------------------------


@router.get("/{session_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(
    session_id: str,
    user: CurrentUser,
    db: DbSession,
) -> list[Artifact]:
    _owned_session(db, user.id, session_id)
    return (
        db.query(Artifact)
        .filter(Artifact.session_id == session_id)
        .order_by(Artifact.created_at.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# GET /sessions/{sid}/artifacts/{aid}/raw[?version=N]
# ---------------------------------------------------------------------------


def _resolve_version(db: DbSession, artifact: Artifact, version: int | None) -> ArtifactVersion:
    """Return the requested version (default: latest). Raises 404 if missing."""
    if version is not None:
        v = (
            db.query(ArtifactVersion)
            .filter(
                ArtifactVersion.artifact_id == artifact.id,
                ArtifactVersion.version == version,
            )
            .first()
        )
        if v is None:
            raise ApiError(404, "not_found", "Version not found")
        return v
    # Latest: ordered desc, limit 1.
    v = (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version.desc())
        .first()
    )
    if v is None:
        raise ApiError(404, "not_found", "No versions found")
    return v


@router.get("/{session_id}/artifacts/{artifact_id}/raw")
def get_artifact_raw(
    session_id: str,
    artifact_id: str,
    user: CurrentUser,
    db: DbSession,
    version: int | None = Query(default=None, ge=1, description="Artifact version; defaults to latest."),
) -> Response:
    _owned_session(db, user.id, session_id)

    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.session_id != session_id:
        raise ApiError(404, "not_found", "Artifact not found")

    av = _resolve_version(db, artifact, version)
    blob = open_blob(av.storage_key)
    content = blob.read()
    blob.close()

    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "private, max-age=300"},
    )
