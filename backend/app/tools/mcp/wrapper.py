"""MCPTool — wraps a single MCP tool as a registry-compatible Tool (§12.2, Task 7.2).

`name` is namespaced as `{server}.{tool}` so MCP tools never collide with
built-in tools.  ctx is accepted to satisfy the protocol but is NOT forwarded
to MCP calls — external tools never receive user scope.
"""
from __future__ import annotations

import asyncio
import json

from mcp import ClientSession, types

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
        try:
            res = await asyncio.wait_for(
                self._session.call_tool(self._tool.name, args),
                timeout=settings.mcp_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise ToolError(f"MCP tool {self.name!r} timed out")

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
