"""MCPManager — connects enabled MCP servers and exposes their tools (Stage 7, Task 7.3).

Each server is connected inside its own try/except so a single broken server
never prevents the others from loading (failure isolation).

Sessions are kept alive via AsyncExitStack.  Call aclose() when done.
"""
from __future__ import annotations

import contextlib
import logging
import os

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

from app.mcp.config import MCPConfig, MCPServerConfig
from app.tools.mcp.wrapper import MCPTool
from app.tools.registry import Tool

log = logging.getLogger(__name__)


class MCPManager:
    def __init__(self, config: MCPConfig) -> None:
        self._config = config
        self._stack = contextlib.AsyncExitStack()
        self._tools: list[Tool] = []

    # ------------------------------------------------------------------
    # Testability seam — tests monkeypatch this to return in-memory sessions
    # ------------------------------------------------------------------

    async def _open_session(self, server: MCPServerConfig) -> ClientSession:
        """Open a live ClientSession for *server* and register cleanup on self._stack.

        For bearer auth the token is read from the environment at connect time.
        """
        headers: dict[str, str] | None = None
        if server.auth.type == "bearer":
            token = os.environ[server.auth.token_env]  # KeyError → isolation catches it
            headers = {"Authorization": f"Bearer {token}"}

        read, write, _ = await self._stack.enter_async_context(
            streamablehttp_client(server.url, headers=headers)
        )
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect_all(self) -> list[Tool]:
        """Connect each enabled server and return all wrapped tools.

        Failures are isolated per-server: a broken server is logged and skipped.
        """
        tools: list[Tool] = []

        for server in self._config.mcp_servers:
            if not server.enabled:
                log.debug("MCP server %r is disabled — skipping.", server.name)
                continue

            try:
                session = await self._open_session(server)
                raw_tools: list[types.Tool] = (await session.list_tools()).tools

                # Filter by allowed_tools if a non-empty allowlist is set
                if server.allowed_tools:
                    raw_tools = [t for t in raw_tools if t.name in server.allowed_tools]

                for tool in raw_tools:
                    tools.append(MCPTool(server.name, tool, session))

                log.info(
                    "MCP server %r connected — %d tool(s) loaded.", server.name, len(raw_tools)
                )
            except Exception:
                log.exception(
                    "MCP server %r failed to connect — skipped.", server.name
                )

        self._tools = tools
        return tools

    def list_tools(self) -> list[Tool]:
        """Return the tools loaded by the last connect_all() call."""
        return list(self._tools)

    async def aclose(self) -> None:
        """Tear down all sessions opened by this manager."""
        await self._stack.aclose()
