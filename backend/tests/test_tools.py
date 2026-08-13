"""Tool registry + search_knowledge_base tests.

Scope-injection security: the schema exposes no user/session/scope params,
and execute() only ever reads scope off ToolContext — never off model args.
We prove cross-session isolation by building two ToolContexts (one per
session) against the same seeded Qdrant collection.
"""
from __future__ import annotations

import asyncio

import pytest
from qdrant_client import QdrantClient

from app.rag.embedder import Embedder, get_embedder
from app.rag.vectors import ensure_collection, upsert_chunks
from app.tools.builtin.search_kb import SearchKnowledgeBase
from app.tools.context import ToolContext
from app.tools.registry import ToolError, ToolRegistry


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return get_embedder()


@pytest.fixture()
def qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


def _make_chunks(user_id: str, session_id: str):
    """Chunks for tool tests. KB chunks are public so they're findable with
    caller_group_ids=[] (which is what search_kb uses when ctx.db is None)."""
    return [
        {
            "content": "Cats are small domesticated carnivorous mammals.",
            "file_id": "file-kb-1",
            "chunk_idx": 0,
            "tags": ["animals"],
            "user_id": user_id,
            "scope": "kb",
            "session_id": None,
            "status": "ready",
            "group_id": None,
            "is_public": True,
        },
        {
            "content": "The quarterly report shows a 12% increase in revenue this session.",
            "file_id": "file-session-1",
            "chunk_idx": 0,
            "tags": ["finance"],
            "user_id": user_id,
            "scope": "session",
            "session_id": session_id,
            "status": "ready",
            "group_id": None,
            "is_public": False,
        },
    ]


# --- Task 4.2 — registry -----------------------------------------------------


class _FakeTool:
    name = "fake_tool"

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": "fake_tool"}}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        return f"ok:{args.get('x')}:{ctx.user_id}"


def test_register_and_schemas_returns_function_list():
    registry = ToolRegistry()
    registry.register(_FakeTool())
    schemas = registry.schemas()
    assert schemas == [{"type": "function", "function": {"name": "fake_tool"}}]


def test_execute_dispatches_by_name():
    registry = ToolRegistry()
    registry.register(_FakeTool())
    ctx = ToolContext(user_id="u1", session_id=None, db=None, client=None, embedder=None)
    result = asyncio.run(registry.execute("fake_tool", {"x": 42}, ctx))
    assert result == "ok:42:u1"


def test_execute_unknown_tool_raises_tool_error():
    registry = ToolRegistry()
    ctx = ToolContext(user_id="u1", session_id=None, db=None, client=None, embedder=None)
    with pytest.raises(ToolError):
        asyncio.run(registry.execute("does_not_exist", {}, ctx))


# --- Task 4.3 — search_kb tool ------------------------------------------------


def test_search_kb_schema_matches_spec_no_scope_params():
    tool = SearchKnowledgeBase()
    schema = tool.schema()
    fn = schema["function"]
    assert schema["type"] == "function"
    assert fn["name"] == "search_knowledge_base"
    props = fn["parameters"]["properties"]
    assert set(props.keys()) == {"query", "tags"}
    assert fn["parameters"]["required"] == ["query"]
    # Security: no scope-controlling params exposed to the model.
    assert "user_id" not in props
    assert "session_id" not in props
    assert "scope" not in props


def test_search_kb_execute_returns_top_chunk_content(
    qdrant: QdrantClient, embedder: Embedder, monkeypatch
):
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
    ensure_collection(qdrant)
    user_id, session_id = "user-1", "session-1"
    upsert_chunks(qdrant, embedder, _make_chunks(user_id, session_id))

    ctx = ToolContext(
        user_id=user_id,
        session_id=session_id,
        db=None,
        client=qdrant,
        embedder=embedder,
    )
    tool = SearchKnowledgeBase()
    result = asyncio.run(tool.execute({"query": "quarterly revenue report"}, ctx))
    assert "quarterly report" in result
    assert "file-session-1" in result


def test_search_kb_execute_filters_by_tags(
    qdrant: QdrantClient, embedder: Embedder, monkeypatch
):
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
    ensure_collection(qdrant)
    user_id = "user-1"
    chunks = [
        {
            "content": "The finance team closed Q1 with strong margins.",
            "file_id": "file-finance",
            "chunk_idx": 0,
            "tags": ["finance"],
            "user_id": user_id,
            "scope": "kb",
            "session_id": None,
            "status": "ready",
            "group_id": None,
            "is_public": True,
        },
        {
            "content": "The HR team rolled out a new onboarding process.",
            "file_id": "file-hr",
            "chunk_idx": 0,
            "tags": ["hr"],
            "user_id": user_id,
            "scope": "kb",
            "session_id": None,
            "status": "ready",
            "group_id": None,
            "is_public": True,
        },
    ]
    upsert_chunks(qdrant, embedder, chunks)

    ctx = ToolContext(
        user_id=user_id,
        session_id=None,
        db=None,
        client=qdrant,
        embedder=embedder,
    )
    tool = SearchKnowledgeBase()

    tagged = asyncio.run(
        tool.execute({"query": "team process margins", "tags": ["finance"]}, ctx)
    )
    assert "file-finance" in tagged
    assert "file-hr" not in tagged

    untagged = asyncio.run(tool.execute({"query": "team process margins"}, ctx))
    assert "file-finance" in untagged
    assert "file-hr" in untagged


def test_search_kb_scope_injected_from_ctx_not_args(
    qdrant: QdrantClient, embedder: Embedder, monkeypatch
):
    """A tool call cannot reach another session's chunks: there's no session
    arg on the schema, and execute() reads session_id off ctx only."""
    monkeypatch.setattr("app.rag.retrieve.settings.rerank_enabled", False)
    ensure_collection(qdrant)
    user_id, session_id = "user-1", "session-1"
    upsert_chunks(qdrant, embedder, _make_chunks(user_id, session_id))

    other_ctx = ToolContext(
        user_id=user_id,
        session_id="other-session",
        db=None,
        client=qdrant,
        embedder=embedder,
    )
    tool = SearchKnowledgeBase()
    # Even if a malicious args payload tried to smuggle a session_id, the
    # schema has no such field and execute() never reads args for scope.
    result = asyncio.run(
        tool.execute({"query": "quarterly revenue report", "session_id": session_id}, other_ctx)
    )
    assert "file-session-1" not in result


def test_search_kb_converts_qdrant_transport_failure_to_tool_error(embedder: Embedder):
    """A dead Qdrant is the model's problem to explain, not the turn's to die on.

    `execute_code` already converts every transport failure into a ToolError;
    this tool raised Qdrant's `ResponseHandlingException` straight through. The
    chat loop now guards broadly as well, so this is defence in depth — but the
    message the model receives should say what is actually unavailable.
    """
    dead = QdrantClient(url="http://127.0.0.1:1", timeout=1)
    ctx = ToolContext(
        user_id="user-1",
        session_id="session-1",
        db=None,
        client=dead,
        embedder=embedder,
    )
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(SearchKnowledgeBase().execute({"query": "anything"}, ctx))
    assert "knowledge base" in str(excinfo.value).lower()
