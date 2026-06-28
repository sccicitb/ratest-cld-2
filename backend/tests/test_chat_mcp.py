"""Chat route tests for MCP tool wiring (Stage 8) — fake tools, no network.

Exercises three scenarios:
1. A registered MCP tool (fake.echo) gets called by the model and the loop
   emits correct `calling_tool` active/complete events.
2. A MCP tool that raises ToolError is fed back as a tool result so the loop
   still finishes with HTTP 200 SSE ending in `done` (no 500).
3. An empty MCP tool list leaves the native-only path unchanged (regression guard).
"""
from __future__ import annotations

import json

from app.chat.client import ModelChunk
from app.chat.routes import get_mcp_tools, get_model_client
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
    """Registry-compatible fake that echoes its args."""

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


def test_mcp_tool_called_emits_calling_tool_events(client, auth_headers):
    """Model calls fake.echo → loop emits calling_tool events and ends done."""
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

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(script)
    app.dependency_overrides[get_mcp_tools] = lambda: [_FakeEchoTool()]
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "echo hello"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)
        app.dependency_overrides.pop(get_mcp_tools, None)

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


def test_mcp_tool_error_feeds_back_and_loop_finishes(client, auth_headers):
    """Model calls a tool that raises ToolError → error fed as result, loop ends done (no 500)."""
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

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(script)
    app.dependency_overrides[get_mcp_tools] = lambda: [_FakeErrorTool()]
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "trigger tool error"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)
        app.dependency_overrides.pop(get_mcp_tools, None)

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    assert events[-1]["type"] == "done"

    # A calling_tool pair must exist (active + complete) even on error
    calling_tool_events = [
        e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
    ]
    assert len(calling_tool_events) == 2


def test_empty_mcp_tools_native_path_unchanged(client, auth_headers):
    """No MCP tools → plain text response still works (regression guard)."""
    sid = _create_session(client, auth_headers)

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        [[ModelChunk(type="text", text="native answer")]]
    )
    app.dependency_overrides[get_mcp_tools] = lambda: []
    try:
        r = client.post(
            f"/api/sessions/{sid}/chat",
            headers=auth_headers,
            json={"message": "hello"},
        )
    finally:
        app.dependency_overrides.pop(get_model_client, None)
        app.dependency_overrides.pop(get_mcp_tools, None)

    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert events[-1]["type"] == "done"
    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events) == "native answer"
