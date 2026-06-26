"""Tool contract + registry (§7) — agentic tools the chat loop can call.

`Tool.schema()` returns an OpenAI-compatible function-tool definition;
`Tool.execute()` is the server-side handler. The registry dispatches by name
and is the single place new tools get wired in.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.tools.context import ToolContext


class ToolError(Exception):
    """Raised when a tool can't be found or fails to execute."""


@runtime_checkable
class Tool(Protocol):
    name: str

    def schema(self) -> dict: ...

    async def execute(self, args: dict, ctx: ToolContext) -> str: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(self, name: str, args: dict, ctx: ToolContext) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool: {name!r}")
        return await tool.execute(args, ctx)
