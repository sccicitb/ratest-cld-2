"""Tests for MCPManager + MCPTool wrapper (Stage 7, Tasks 7.2–7.3).

Uses FastMCP + mcp.shared.memory for in-memory sessions (no network).
Async tests use asyncio.run() to match the existing test suite's convention.
"""
from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as mem

from app.mcp.config import MCPConfig, MCPServerConfig
from app.mcp.manager import MCPManager
from app.tools.registry import ToolError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_server() -> FastMCP:
    """Build a FastMCP server with two tools: echo and add."""
    server = FastMCP("fake")

    @server.tool()
    def echo(text: str) -> str:
        """Return the text unchanged."""
        return text

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    return server


def _single_server_config(name: str = "fake", allowed_tools: list[str] | None = None) -> MCPConfig:
    return MCPConfig(
        mcp_servers=[
            MCPServerConfig(
                name=name,
                url="http://fake/mcp",
                enabled=True,
                allowed_tools=allowed_tools or [],
            )
        ]
    )


def _two_server_config() -> MCPConfig:
    return MCPConfig(
        mcp_servers=[
            MCPServerConfig(name="bad", url="http://bad/mcp", enabled=True),
            MCPServerConfig(name="good", url="http://good/mcp", enabled=True),
        ]
    )


def _patch_open_session(manager: MCPManager, server_sessions: dict) -> None:
    """Monkeypatch manager._open_session to return in-memory sessions.

    server_sessions: {server_name: FastMCP | Exception}
    If the value is an Exception, _open_session raises it for that server.
    """
    async def fake_open_session(server_cfg: MCPServerConfig):
        val = server_sessions.get(server_cfg.name)
        if isinstance(val, Exception):
            raise val
        if val is None:
            raise RuntimeError(f"No session configured for {server_cfg.name!r}")
        session = await manager._stack.enter_async_context(mem(val))
        return session

    manager._open_session = fake_open_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_namespacing():
    """connect_all returns tools with names 'server.tool_name'."""
    async def run():
        fake_server = _make_fake_server()
        mgr = MCPManager(_single_server_config("fake"))
        _patch_open_session(mgr, {"fake": fake_server})
        tools = await mgr.connect_all()
        await mgr.aclose()
        return {t.name for t in tools}

    names = asyncio.run(run())
    assert "fake.echo" in names
    assert "fake.add" in names


def test_schema_is_openai_shaped():
    """schema() returns the OpenAI function-tool shape with correct parameters."""
    async def run():
        fake_server = _make_fake_server()
        mgr = MCPManager(_single_server_config("fake"))
        _patch_open_session(mgr, {"fake": fake_server})
        tools = await mgr.connect_all()
        await mgr.aclose()
        return tools

    tools = asyncio.run(run())
    echo_tool = next(t for t in tools if t.name == "fake.echo")
    schema = echo_tool.schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "fake.echo"
    assert "description" in fn
    params = fn["parameters"]
    assert params.get("type") == "object"
    assert "text" in params.get("properties", {})


def test_execute_roundtrip():
    """execute({'text': 'hi'}, ctx) returns 'hi' (round-trip through in-memory server)."""
    async def run():
        fake_server = _make_fake_server()
        mgr = MCPManager(_single_server_config("fake"))
        _patch_open_session(mgr, {"fake": fake_server})
        tools = await mgr.connect_all()
        echo_tool = next(t for t in tools if t.name == "fake.echo")
        result = await echo_tool.execute({"text": "hi"}, ctx=None)
        await mgr.aclose()
        return result

    assert asyncio.run(run()) == "hi"


def test_allowed_tools_filter():
    """allowed_tools=['echo'] leaves only fake.echo, not fake.add."""
    async def run():
        fake_server = _make_fake_server()
        mgr = MCPManager(_single_server_config("fake", allowed_tools=["echo"]))
        _patch_open_session(mgr, {"fake": fake_server})
        tools = await mgr.connect_all()
        await mgr.aclose()
        return {t.name for t in tools}

    names = asyncio.run(run())
    assert "fake.echo" in names
    assert "fake.add" not in names


def test_failure_isolation():
    """One server raising in _open_session doesn't prevent other server's tools."""
    async def run():
        good_mcp = FastMCP("good")

        @good_mcp.tool()
        def ping() -> str:
            """Ping."""
            return "pong"

        mgr = MCPManager(_two_server_config())
        _patch_open_session(
            mgr,
            {
                "bad": RuntimeError("connection refused"),
                "good": good_mcp,
            },
        )
        tools = await mgr.connect_all()
        await mgr.aclose()
        return {t.name for t in tools}

    names = asyncio.run(run())
    assert "good.ping" in names
    assert not any(n.startswith("bad.") for n in names)


def test_iserror_raises_tool_error():
    """A tool returning an MCP error (isError=True) raises ToolError."""
    async def run():
        err_server = FastMCP("errsrv")

        @err_server.tool()
        def boom() -> str:
            """Always fails."""
            raise ValueError("something went wrong")

        cfg = MCPConfig(
            mcp_servers=[
                MCPServerConfig(name="errsrv", url="http://err/mcp", enabled=True)
            ]
        )
        mgr = MCPManager(cfg)
        _patch_open_session(mgr, {"errsrv": err_server})
        tools = await mgr.connect_all()
        boom_tool = next(t for t in tools if t.name == "errsrv.boom")
        try:
            await boom_tool.execute({}, ctx=None)
            raised = False
        except ToolError:
            raised = True
        await mgr.aclose()
        return raised

    assert asyncio.run(run()) is True


def test_disabled_server_is_skipped():
    """A server with enabled=False is skipped entirely — _open_session never called."""
    async def run():
        cfg = MCPConfig(
            mcp_servers=[
                MCPServerConfig(name="off", url="http://off/mcp", enabled=False)
            ]
        )
        mgr = MCPManager(cfg)
        called = []

        async def fake_open_session(server_cfg):
            called.append(server_cfg.name)
            raise RuntimeError("should not be called")

        mgr._open_session = fake_open_session
        tools = await mgr.connect_all()
        await mgr.aclose()
        return tools, called

    tools, called = asyncio.run(run())
    assert tools == []
    assert called == []
