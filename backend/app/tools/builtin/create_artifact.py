"""`create_artifact` — model-authored HTML report artifacts (§v1.1 Stage A1).

The model calls this with a `title` and `html` body, optionally an
`artifact_id` to update an existing artifact (versioned). Ownership is
scoped to the current session; `artifact_id` must belong to `ctx.session_id`
or a ToolError is raised.

The tool writes the HTML to a blob file (blob_dir/<uuid>.html) and records
an Artifact + ArtifactVersion row (or increments the version for an update).
It stashes metadata on `ctx.pending_artifacts` so the chat loop can emit the
`artifact` SSE event and link the version to the final assistant message.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.config import settings
from app.models import Artifact, ArtifactVersion
from app.tools.context import ToolContext
from app.tools.registry import ToolError


class CreateArtifact:
    name = "create_artifact"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "create_artifact",
                "description": (
                    "Create or update a versioned HTML report artifact. "
                    "Use this to author a standalone HTML report (e.g. chart "
                    "dashboard, summary table, PDF-ready page) that the user "
                    "can view, download, and print. Call with a descriptive "
                    "`title` and a complete, self-contained `html` document "
                    "(including inline CSS). To update an existing report, "
                    "pass its `artifact_id` — a new version will be stored "
                    "and the old version remains reachable."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Descriptive title for this report artifact.",
                        },
                        "html": {
                            "type": "string",
                            "description": (
                                "Complete, self-contained HTML document "
                                "(<!DOCTYPE html>…</html>) including any "
                                "inline CSS. Must be valid HTML5."
                            ),
                        },
                        "artifact_id": {
                            "type": "string",
                            "description": (
                                "The artifact to update. Omit when creating "
                                "a new artifact; provide a previously-returned "
                                "artifact_id to add a new version."
                            ),
                        },
                    },
                    "required": ["title", "html"],
                },
            },
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        session_id = ctx.session_id
        if not session_id:
            raise ToolError(
                "create_artifact requires a session context (no session_id)"
            )

        title = args.get("title")
        html = args.get("html")
        artifact_id = args.get("artifact_id")

        # Validate inputs.
        if not title or not isinstance(title, str) or not title.strip():
            raise ToolError("title must be a non-empty string")
        if not html or not isinstance(html, str) or not html.strip():
            raise ToolError("html must be a non-empty string")

        title = title.strip()
        html = html.strip()
        db = ctx.db

        # ---- Update existing artifact ----
        if artifact_id:
            artifact = db.get(Artifact, artifact_id)
            if artifact is None:
                raise ToolError(
                    f"artifact {artifact_id!r} not found"
                )
            if artifact.session_id != session_id:
                raise ToolError(
                    f"artifact {artifact_id!r} does not belong to this session"
                )
            # Increment version.
            new_version = artifact.latest_version + 1
            artifact.latest_version = new_version
        else:
            # ---- Create new artifact ----
            artifact = Artifact(
                session_id=session_id,
                title=title,
                latest_version=1,
            )
            db.add(artifact)
            db.flush()  # assign artifact.id
            new_version = 1

        # Write the HTML blob.
        storage_key = f"{uuid.uuid4().hex}.html"
        blob_dir = Path(settings.blob_dir)
        blob_dir.mkdir(parents=True, exist_ok=True)
        (blob_dir / storage_key).write_text(html, encoding="utf-8")

        # Record the version.
        version = ArtifactVersion(
            artifact_id=artifact.id,
            version=new_version,
            storage_key=storage_key,
        )
        db.add(version)
        db.commit()
        db.refresh(version)

        # Stash for the chat loop so it can emit the artifact SSE event and
        # link this version to the final assistant message.
        ctx.pending_artifacts.append(
            {
                "version_id": version.id,
                "artifact_id": artifact.id,
                "version": new_version,
                "title": title,
            }
        )

        return json.dumps(
            {"artifact_id": artifact.id, "version": new_version},
            separators=(",", ":"),
        )
