"""Per-caller MCP tool resolution (§M4b.2).

`resolve_caller_mcp_tools` queries the M4a catalog for the DISTINCT ENABLED
MCP servers granted to the caller's groups, lists their tools via a resilient
short-lived connection (reusing `_list_tools` from verify.py), and returns
config-based `MCPTool` instances ready for registration in the turn registry.

Failure isolation: a down or erroring server contributes zero tools and logs
a warning — it never raises and never blocks other servers.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.groups.service import group_ids_for
from app.mcp.verify import _list_tools
from app.models import MCPServer, User, group_mcp
from app.tools.mcp.wrapper import MCPTool
from app.tools.registry import Tool

log = logging.getLogger(__name__)

# Short timeout for the per-turn list-tools handshake: this runs on EVERY chat
# turn before the first token, so a down granted server must fail fast (not the
# longer tool-call timeout). A user with an unreachable server stalls at most
# this long per server.
RESOLVE_TIMEOUT_SECONDS: float = 5.0


async def resolve_caller_mcp_tools(
    db: Session,
    user: User,
    *,
    timeout: float = RESOLVE_TIMEOUT_SECONDS,
) -> list[Tool]:
    """Return the MCP tools available to *user* for this turn.

    Steps:
    1. Resolve the caller's group IDs.
    2. Query DISTINCT enabled MCPServers granted to any of those groups.
    3. Per-server (with failure isolation): decrypt token, list tools via
       a fresh connection, build namespaced MCPTool instances.

    Returns [] when the user belongs to no groups or has no granted servers.
    Never raises — a failing server is logged and skipped.
    """
    group_ids = group_ids_for(user)
    if not group_ids:
        return []

    stmt = (
        select(MCPServer)
        .join(group_mcp, group_mcp.c.mcp_server_id == MCPServer.id)
        .where(group_mcp.c.group_id.in_(group_ids))
        .where(MCPServer.enabled.is_(True))
        .distinct()
    )
    servers: list[MCPServer] = list(db.execute(stmt).scalars().all())

    if not servers:
        return []

    tools: list[Tool] = []
    for server in servers:
        try:
            headers: dict[str, str] | None = None
            if server.auth_type == "bearer" and server.token_encrypted:
                from app.mcp.crypto import decrypt_token  # local import avoids circular

                plain = decrypt_token(server.token_encrypted)
                headers = {"Authorization": f"Bearer {plain}"}

            mcp_tools = await _list_tools(
                url=server.url,
                transport=server.transport,
                headers=headers,
                timeout=timeout,
            )

            for tool in mcp_tools:
                tools.append(
                    MCPTool(
                        server_name=server.name,
                        tool_name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.inputSchema,
                        url=server.url,
                        transport=server.transport,
                        headers=headers,
                    )
                )
            log.debug(
                "MCP server %r resolved %d tool(s) for user %s.",
                server.name,
                len(mcp_tools),
                user.id,
            )
        except Exception:
            log.warning(
                "MCP server %r failed during resolve for user %s — skipped.",
                server.name,
                user.id,
                exc_info=True,
            )

    return tools
