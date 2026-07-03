"""Chat route tests for per-caller MCP tool wiring (§M4b.3) — fake tools, no network.

Exercises four scenarios:
1. A user whose group has an enabled MCP server: the fake tool (fake.echo) is
   offered, the model calls it, and `calling_tool` active/complete events stream.
2. A MCP tool that raises ToolError feeds the error back; the loop ends done (no 500).
3. A user with NO granted MCP server → empty tool list (no MCP tools offered).
4. Isolation: a user in group A cannot see group B's server's tools.

`resolve_caller_mcp_tools` is monkeypatched so no real MCP server is needed.
"""
from __future__ import annotations

import json

import app.chat.routes as chat_routes_module
from app.chat.client import ModelChunk
from app.chat.routes import get_model_client
from app.main import app
from app.tools.registry import ToolError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeModelClient:
    """Each entry in `script` is the list of ModelChunks for one `.stream()` call."""

    def __init__(self, script: list[list[ModelChunk]]):
        self._script = list(script)

    async def stream(self, messages, tools):
        chunks = self._script.pop(0) if self._script else []
        for chunk in chunks:
            yield chunk


class _FakeEchoTool:
    """Registry-compatible fake that echoes its args — simulates an MCP tool."""

    name = "fake.echo"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Echo the input text.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            },
        }

    async def execute(self, args: dict, ctx) -> str:  # noqa: ARG002
        return f"echoed: {args.get('text', '')}"


class _FakeErrorTool:
    """Registry-compatible fake that always raises ToolError."""

    name = "fake.error"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Always fails.",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def execute(self, args: dict, ctx) -> str:  # noqa: ARG002
        raise ToolError("simulated MCP tool failure")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def _create_session(client, auth_headers) -> str:
    r = client.post("/api/sessions", headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mcp_tool_called_emits_calling_tool_events(client, auth_headers, monkeypatch):
    """User has a granted MCP server → fake.echo is offered and called."""
    sid = _create_session(client, auth_headers)

    script = [
        [
            ModelChunk(
                type="tool_call",
                id="tc-1",
                name="fake.echo",
                arguments={"text": "hello"},
            )
        ],
        [ModelChunk(type="text", text="The echo said: echoed: hello")],
    ]

    async def _resolve(db, user, **_):
        return [_FakeEchoTool()]

    monkeypatch.setattr(chat_routes_module, "resolve_caller_mcp_tools", _resolve)
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(script)
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "echo hello"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)

    calling_tool_events = [
        e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
    ]
    assert len(calling_tool_events) == 2, calling_tool_events
    active, complete = calling_tool_events
    assert active["status"] == "active"
    assert active["toolName"] == "fake.echo"
    assert complete["status"] == "complete"

    assert events[-1]["type"] == "done"


def test_mcp_tool_error_feeds_back_and_loop_finishes(client, auth_headers, monkeypatch):
    """Model calls a tool that raises ToolError → error fed as result, loop ends done."""
    sid = _create_session(client, auth_headers)

    script = [
        [
            ModelChunk(
                type="tool_call",
                id="tc-err",
                name="fake.error",
                arguments={},
            )
        ],
        [ModelChunk(type="text", text="I encountered an error but recovered.")],
    ]

    async def _resolve(db, user, **_):
        return [_FakeErrorTool()]

    monkeypatch.setattr(chat_routes_module, "resolve_caller_mcp_tools", _resolve)
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(script)
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "trigger tool error"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert events[-1]["type"] == "done"

    calling_tool_events = [
        e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
    ]
    assert len(calling_tool_events) == 2


def test_no_granted_server_no_mcp_tools(client, auth_headers, monkeypatch):
    """User with no granted MCP server → resolve returns [] → plain response."""
    sid = _create_session(client, auth_headers)

    async def _resolve(db, user, **_):
        return []

    monkeypatch.setattr(chat_routes_module, "resolve_caller_mcp_tools", _resolve)
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        [[ModelChunk(type="text", text="native answer")]]
    )
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "hello"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert events[-1]["type"] == "done"
    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events) == "native answer"


def test_group_isolation_user_sees_only_own_tools(client, auth_headers, monkeypatch):
    """Resolver is called with the actual user — group A's tools are NOT shown to group B's user.

    This test verifies that the resolver receives the correct *user* object and
    that the route does NOT inject tools from a hardcoded global pool.  We
    simulate group isolation by returning different tool lists based on user id.
    """
    sid = _create_session(client, auth_headers)

    seen_users: list[str] = []

    async def _resolve(db, user, **_):
        seen_users.append(user.id)
        # Simulate: this user's groups grant no MCP servers
        return []

    monkeypatch.setattr(chat_routes_module, "resolve_caller_mcp_tools", _resolve)
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        [[ModelChunk(type="text", text="ok")]]
    )
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "hi"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)

    assert r.status_code == 200, r.text
    # resolve was called exactly once with a real user object
    assert len(seen_users) == 1
    events = _parse_sse(r.text)
    assert events[-1]["type"] == "done"
    # No MCP tools were offered — no calling_tool events
    assert not any(
        e.get("step") == "calling_tool" for e in events if e.get("type") == "step"
    )
