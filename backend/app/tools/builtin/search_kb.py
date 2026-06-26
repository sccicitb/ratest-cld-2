"""`search_knowledge_base` — the retrieval tool exposed to the model (§7).

Scope (`user_id`, `session_id`) comes exclusively from `ToolContext`, which is
built server-side from the authenticated request/session — never from the
model's tool-call `args`. The schema below deliberately has no user/session/
scope parameter, so the model has no surface to even attempt it.
"""
from __future__ import annotations

from app.rag.retrieve import retrieve
from app.tools.context import ToolContext

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
        chunks = retrieve(
            query=query,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            client=ctx.client,
            embedder=ctx.embedder,
            k=DEFAULT_K,
        )
        if not chunks:
            return "No matching documents found."
        parts = [
            f"[{chunk['file_id']}#{chunk['chunk_idx']}] {chunk['content']}"
            for chunk in chunks
        ]
        return "\n\n".join(parts)
