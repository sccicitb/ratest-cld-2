"""MCPTool — wraps a single MCP tool as a registry-compatible Tool (§12.2, Task 7.2).

`name` is namespaced as `{server}.{tool}` so MCP tools never collide with
built-in tools.  ctx is accepted to satisfy the protocol but is NOT forwarded
to MCP calls — external tools never receive user scope.
"""
from __future__ import annotations

import json
from datetime import timedelta

from mcp import ClientSession, McpError, types

from app.config import settings
from app.tools.context import ToolContext  # noqa: F401  (protocol compat only)
from app.tools.registry import ToolError

_EMPTY_PARAMS: dict = {"type": "object", "properties": {}}


class MCPTool:
    """Registry-compatible adapter for a single tool exposed by an MCP server."""

    def __init__(self, server_name: str, tool: types.Tool, session: ClientSession) -> None:
        self.name = f"{server_name}.{tool.name}"
        self._tool = tool
        self._session = session

    def schema(self) -> dict:
        """OpenAI function-tool shape with MCP inputSchema as parameters."""
        params = self._tool.inputSchema or _EMPTY_PARAMS
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self._tool.description or "",
                "parameters": params,
            },
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:  # noqa: ARG002
        """Call the MCP tool and return its text output.

        ctx is deliberately unused — external MCP tools never receive user scope.
        Raises ToolError if the MCP server signals isError.
        """
        # Use the SDK's native per-request timeout (anyio.fail_after inside the
        # session's own task scope) rather than asyncio.wait_for — wrapping from
        # an outer task cancels across the anyio scope boundary (noisy warnings,
        # and risks the long-lived shared session). On timeout the SDK raises
        # McpError("Timed out ...").
        try:
            res = await self._session.call_tool(
                self._tool.name,
                args,
                read_timeout_seconds=timedelta(seconds=settings.mcp_tool_timeout_seconds),
            )
        except McpError as exc:
            raise ToolError(f"MCP tool {self.name!r} failed: {exc}")

        # Collect text from TextContent blocks
        texts = [
            block.text
            for block in (res.content or [])
            if isinstance(block, types.TextContent)
        ]
        text = "\n".join(texts).strip()

        if res.isError:
            raise ToolError(text or "MCP tool returned an error")

        if not text and res.structuredContent:
            text = json.dumps(res.structuredContent)

        return text
