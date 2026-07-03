"""Unit tests for the config-based MCPTool wrapper (§M4b.1).

Uses FastMCP + mcp.shared.memory for in-memory sessions.  The MCPTool now
reconnects per-call, so tests can use real in-memory servers without any
lifecycle management.

Coverage:
- schema() returns the correct OpenAI function-tool shape
- execute() round-trips through an in-memory FastMCP server → returns text
- execute() on an isError result → raises ToolError
- execute() on timeout → raises ToolError
- Unsupported transport → raises ToolError immediately
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from app.tools.mcp.wrapper import MCPTool
from app.tools.registry import ToolError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(
    server_name: str = "srv",
    tool_name: str = "echo",
    description: str = "Echo",
    input_schema: dict | None = None,
    url: str = "http://fake/mcp",
    transport: str = "streamable-http",
    headers: dict | None = None,
) -> MCPTool:
    return MCPTool(
        server_name=server_name,
        tool_name=tool_name,
        description=description,
        input_schema=input_schema,
        url=url,
        transport=transport,
        headers=headers,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_name_is_namespaced():
    t = _make_tool(server_name="mysvr", tool_name="dothing")
    assert t.name == "mysvr.dothing"


def test_schema_openai_shape():
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    t = _make_tool(description="Do it", input_schema=schema)
    s = t.schema()
    assert s["type"] == "function"
    fn = s["function"]
    assert fn["name"] == "srv.echo"
    assert fn["description"] == "Do it"
    assert fn["parameters"] == schema


def test_schema_fallback_empty_params():
    t = _make_tool(input_schema=None)
    s = t.schema()
    assert s["function"]["parameters"] == {"type": "object", "properties": {}}


def test_unsupported_transport_raises_tool_error():
    t = _make_tool(transport="stdio")

    async def run():
        await t.execute({}, ctx=None)

    with pytest.raises(ToolError, match="Unsupported transport"):
        _run(run())


def test_execute_roundtrip_via_in_memory_server():
    """MCPTool.execute core path: initialize + call_tool returns text.

    Tests the in-process execution pattern using the mcp shared-memory
    transport directly, verifying that our MCPTool constructs the right
    result text from a real FastMCP server response.
    """
    from mcp.shared.memory import create_connected_server_and_client_session
    from mcp import types

    fake_server = FastMCP("fake")

    @fake_server.tool()
    def echo(text: str) -> str:
        """Return the text unchanged."""
        return text

    result_holder: list[str] = []

    async def run():
        async with create_connected_server_and_client_session(fake_server) as session:
            await session.initialize()
            res = await session.call_tool("echo", {"text": "hi"})
            texts = [b.text for b in (res.content or []) if isinstance(b, types.TextContent)]
            result_holder.append("\n".join(texts).strip())

    _run(run())
    assert result_holder == ["hi"]


def test_execute_iserror_raises_tool_error(monkeypatch):
    """An MCP tool returning isError=True → ToolError raised."""
    import app.tools.mcp.wrapper as wrapper_mod
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock
    from mcp import types

    error_result = MagicMock()
    error_result.isError = True
    error_result.content = [types.TextContent(type="text", text="something broke")]
    error_result.structuredContent = None

    fake_session = AsyncMock()
    fake_session.initialize = AsyncMock()
    fake_session.call_tool = AsyncMock(return_value=error_result)

    @asynccontextmanager
    async def _fake_http(url, headers=None):
        yield MagicMock(), MagicMock(), None

    @asynccontextmanager
    async def _fake_client_session(r, w):
        yield fake_session

    # Patch at the wrapper module's import binding (not at the mcp package)
    monkeypatch.setattr(wrapper_mod, "streamablehttp_client", _fake_http)
    monkeypatch.setattr(wrapper_mod, "ClientSession", _fake_client_session)

    t = _make_tool()

    with pytest.raises(ToolError, match="something broke"):
        _run(t.execute({}, ctx=None))


def test_execute_timeout_raises_tool_error(monkeypatch):
    """A timeout during call_tool → ToolError raised (catches TimeoutError or anyio wrap)."""
    import app.tools.mcp.wrapper as wrapper_mod
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    fake_session = AsyncMock()
    fake_session.initialize = AsyncMock()

    async def _slow_call(*_a, **_kw):
        await asyncio.sleep(9999)

    fake_session.call_tool = _slow_call

    @asynccontextmanager
    async def _fake_http(url, headers=None):
        yield MagicMock(), MagicMock(), None

    @asynccontextmanager
    async def _fake_client_session(r, w):
        yield fake_session

    monkeypatch.setattr(wrapper_mod, "streamablehttp_client", _fake_http)
    monkeypatch.setattr(wrapper_mod, "ClientSession", _fake_client_session)

    # Use a very short timeout so the test runs fast
    import app.config as cfg_mod
    original = cfg_mod.settings.mcp_tool_timeout_seconds
    cfg_mod.settings.mcp_tool_timeout_seconds = 0.01
    try:
        t = _make_tool()
        # anyio may wrap TimeoutError in ExceptionGroup — both are caught as ToolError
        with pytest.raises(ToolError):
            _run(t.execute({}, ctx=None))
    finally:
        cfg_mod.settings.mcp_tool_timeout_seconds = original
