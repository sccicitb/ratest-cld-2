"""`search_knowledge_base` — the retrieval tool exposed to the model (§7).

Scope (`user_id`, `session_id`) comes exclusively from `ToolContext`, which is
built server-side from the authenticated request/session — never from the
model's tool-call `args`. The schema below deliberately has no user/session/
scope parameter, so the model has no surface to even attempt it.
"""
from __future__ import annotations

from app.groups.service import group_ids_for
from app.models import User
from app.rag.retrieve import retrieve
from app.tools.context import ToolContext
from app.tools.registry import ToolError

DEFAULT_K = 5


class SearchKnowledgeBase:
    name = "search_knowledge_base"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "search_knowledge_base",
                "description": (
                    "Search the user's indexed documents. Call this whenever the "
                    "answer may depend on the user's files, recent data, or "
                    "anything not already in the conversation. May be called "
                    "multiple times with refined queries."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Semantic search query",
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        query = args["query"]
        tags = args.get("tags")
        # Resolve the caller's groups server-side (never from model args) — the
        # KB access filter is `is_public OR group_id ∈ caller_group_ids` (§8/M3).
        # ctx.db may be None in lightweight contexts (tests, streaming tasks that
        # have no DB session); treat that as no-group (public-only KB access).
        if ctx.db is not None:
            user = ctx.db.get(User, ctx.user_id)
            caller_group_ids = group_ids_for(user) if user is not None else []
        else:
            caller_group_ids = []
        try:
            chunks = retrieve(
                query=query,
                user_id=ctx.user_id,
                session_id=ctx.session_id,
                caller_group_ids=caller_group_ids,
                client=ctx.client,
                embedder=ctx.embedder,
                k=DEFAULT_K,
                tags=tags,
            )
        except Exception as exc:  # noqa: BLE001 — becomes a tool result, not a crash
            # Qdrant being unreachable raises `ResponseHandlingException`, which
            # is not a ToolError; before this it unwound the entire chat turn.
            # Same discipline as `execute_code`: name what is unavailable so the
            # model can tell the user rather than failing the whole answer.
            raise ToolError(f"knowledge base unavailable: {exc}") from exc
        if not chunks:
            return "No matching documents found."
        parts = [
            f"[{chunk['file_id']}#{chunk['chunk_idx']}] {chunk['content']}"
            for chunk in chunks
        ]
        return "\n\n".join(parts)
