"""Resilient MCP server probe — the core of M4a (§M4a.3).

probe_server() does the FULL handshake in one coroutine with nested async-with
so the anyio task-group / cancel-scope stays in this task. It never raises —
a down/unauthorized/timeout server returns ProbeResult(ok=False, ...).

probe_config() is the convenience wrapper used by admin endpoints.

_list_tools() is the shared raw connect+list helper used by both probe_server
and resolve_caller_mcp_tools (M4b) so the resilient pattern lives in one place.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

from app.models import MCPServer


PROBE_TIMEOUT_SECONDS: float = 15.0


@dataclass
class ProbeResult:
    ok: bool
    tools: list[str] = field(default_factory=list)
    error: str | None = None


async def _list_tools(
    *,
    url: str,
    transport: str,
    headers: dict[str, str] | None,
    timeout: float,
) -> list[types.Tool]:
    """Connect, initialize, and list tools — returns the tool list or raises.

    Callers are responsible for catching exceptions (probe_server wraps in
    try/except; resolve_caller_mcp_tools does per-server try/except).
    """
    if transport != "streamable-http":
        raise ValueError(f"Unsupported transport: {transport!r}")
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), timeout)
            result = await asyncio.wait_for(s.list_tools(), timeout)
    return result.tools


async def probe_server(
    *,
    url: str,
    transport: str,
    headers: dict[str, str] | None,
    timeout: float,
) -> ProbeResult:
    """Connect, initialize, and list tools — all inside one coroutine.

    Returns ProbeResult(ok=True, tools=[...]) on success.
    Returns ProbeResult(ok=False, error=<reason>) on any failure.
    Never raises.
    """
    try:
        tools = await _list_tools(url=url, transport=transport, headers=headers, timeout=timeout)
        return ProbeResult(ok=True, tools=[t.name for t in tools], error=None)
    except Exception as exc:  # ConnectError / McpError / TimeoutError / ValueError / anything
        reason = str(exc) or type(exc).__name__
        return ProbeResult(ok=False, tools=[], error=reason)


async def probe_config(
    server: MCPServer,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Build headers from server auth config and run probe_server."""
    headers: dict[str, str] | None = None
    if server.auth_type == "bearer" and server.token_encrypted:
        from app.mcp.crypto import decrypt_token  # local import avoids circular

        plain = decrypt_token(server.token_encrypted)
        headers = {"Authorization": f"Bearer {plain}"}
    return await probe_server(
        url=server.url,
        transport=server.transport,
        headers=headers,
        timeout=timeout,
    )
