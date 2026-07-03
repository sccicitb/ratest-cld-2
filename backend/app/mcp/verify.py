"""Resilient MCP server probe — the core of M4a (§M4a.3).

probe_server() does the FULL handshake in one coroutine with nested async-with
so the anyio task-group / cancel-scope stays in this task. It never raises —
a down/unauthorized/timeout server returns ProbeResult(ok=False, ...).

probe_config() is the convenience wrapper used by admin endpoints.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.models import MCPServer


PROBE_TIMEOUT_SECONDS: float = 15.0


@dataclass
class ProbeResult:
    ok: bool
    tools: list[str] = field(default_factory=list)
    error: str | None = None


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
    if transport != "streamable-http":
        return ProbeResult(ok=False, tools=[], error=f"Unsupported transport: {transport!r}")
    try:
        async with streamablehttp_client(url, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as s:
                await asyncio.wait_for(s.initialize(), timeout)
                result = await asyncio.wait_for(s.list_tools(), timeout)
        return ProbeResult(ok=True, tools=[t.name for t in result.tools], error=None)
    except Exception as exc:  # ConnectError / McpError / TimeoutError / anything
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
