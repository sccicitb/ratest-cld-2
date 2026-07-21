"""MCPTool — config-based, anyio-safe single-tool adapter (§M4b.1).

Carries the server's CONNECTION CONFIG + the tool's schema.  Each `execute()`
opens a SHORT-LIVED connection just for that call and tears it down immediately
— no session is held across the SSE generator's yields (the anyio-safety fix).

`name` is namespaced as `{server_name}__{tool_name}` so MCP tools never
collide with built-ins.  `ctx` is accepted to satisfy the protocol but is NOT
forwarded to MCP calls — external tools never receive user scope.
"""
from __future__ import annotations

import asyncio
import json

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings
from app.tools.context import ToolContext  # noqa: F401  (protocol compat only)
from app.tools.registry import ToolError

_EMPTY_PARAMS: dict = {"type": "object", "properties": {}}


class MCPTool:
    """Registry-compatible adapter that reconnects per-call (anyio-safe)."""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict | None,
        url: str,
        transport: str,
        headers: dict[str, str] | None,
    ) -> None:
        # Separator is "__" (not ".") so the exposed function name stays within
        # the OpenAI/DeepSeek tool-name regex ^[a-zA-Z0-9_-]{1,64}$. A dot is
        # accepted by lenient servers (llama-server) but 400s strict hosted APIs.
        self.name = f"{server_name}__{tool_name}"
        self._tool_name = tool_name
        self._description = description
        self._input_schema = input_schema
        self._url = url
        self._transport = transport
        self._headers = headers

    def schema(self) -> dict:
        """OpenAI function-tool shape with MCP inputSchema as parameters."""
        params = self._input_schema or _EMPTY_PARAMS
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self._description or "",
                "parameters": params,
            },
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:  # noqa: ARG002
        """Call the MCP tool via a short-lived connection.

        Opens → initialises → calls → closes, all within this coroutine.
        No session outlives this call (anyio-safe for SSE generators).
        ctx is deliberately unused — external MCP tools never receive user scope.
        Raises ToolError on isError / timeout / any connection failure.
        """
        if self._transport != "streamable-http":
            raise ToolError(f"Unsupported transport: {self._transport!r}")

        try:
            async with streamablehttp_client(self._url, headers=self._headers) as (r, w, _):
                async with ClientSession(r, w) as s:
                    # Guard the handshake too — a half-up server (TCP connects,
                    # MCP hangs) must not freeze the SSE turn indefinitely.
                    await asyncio.wait_for(s.initialize(), settings.mcp_tool_timeout_seconds)
                    res = await asyncio.wait_for(
                        s.call_tool(self._tool_name, args),
                        settings.mcp_tool_timeout_seconds,
                    )
        except asyncio.TimeoutError as exc:
            raise ToolError(f"MCP tool {self.name!r} timed out") from exc
        except Exception as exc:
            raise ToolError(f"MCP tool {self.name!r} failed: {exc}") from exc

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
