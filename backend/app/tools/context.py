"""ToolContext — server-controlled values the model must never set itself.

`user_id` and `session_id` define the security scope for any tool that
touches retrieval; they come from the authenticated request, never from
model-supplied tool-call `args`. See `app/tools/builtin/search_kb.py`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient

from app.rag.embedder import Embedder


@dataclass
class ToolContext:
    user_id: str
    session_id: str | None
    db: Any
    client: QdrantClient
    embedder: Embedder
    on_progress: Callable[[str], None] | None = None
    pending_artifacts: list[dict] = field(default_factory=list)
    """Populated by `create_artifact`; drained by the loop for SSE events + message linkage."""
