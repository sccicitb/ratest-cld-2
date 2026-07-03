"""Admin MCP server catalog endpoints (§M4a). All require AdminUser."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.exc import IntegrityError

from app.auth.deps import AdminUser, DbSession
from app.errors import ApiError
from app.models import Group, MCPServer
from app.mcp.crypto import encrypt_token
from app.mcp.verify import probe_config
from app.schemas import (
    CreateMCPServerRequest,
    GroupDetailOut,
    MCPServerOut,
    SetGroupServersRequest,
    UpdateMCPServerRequest,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_server_or_404(server_id: str, db: DbSession) -> MCPServer:
    server = db.get(MCPServer, server_id)
    if not server:
        raise ApiError(404, "not_found", "MCP server not found")
    return server


# ---------------------------------------------------------------------------
# MCP Server CRUD
# ---------------------------------------------------------------------------


@router.post("/mcp-servers", response_model=MCPServerOut, status_code=201)
async def create_mcp_server(
    body: CreateMCPServerRequest, _admin: AdminUser, db: DbSession
) -> MCPServerOut:
    token_enc: str | None = None
    if body.auth_type == "bearer":
        if not body.token:
            raise ApiError(400, "token_required", "token is required when authType is bearer")
        token_enc = encrypt_token(body.token)

    server = MCPServer(
        name=body.name,
        transport=body.transport,
        url=body.url,
        auth_type=body.auth_type,
        token_encrypted=token_enc,
        enabled=False,  # always create disabled first; enable after probe passes
    )
    db.add(server)
    try:
        db.flush()  # catch unique constraint before the probe
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "name_taken", "An MCP server with that name already exists")

    if body.enabled:
        result = await probe_config(server)
        if not result.ok:
            db.rollback()
            raise ApiError(400, "probe_failed", result.error or "Probe failed")
        server.enabled = True

    db.commit()
    db.refresh(server)
    return MCPServerOut.model_validate(server)


@router.get("/mcp-servers", response_model=list[MCPServerOut])
def list_mcp_servers(_admin: AdminUser, db: DbSession) -> list[MCPServerOut]:
    servers = db.query(MCPServer).order_by(MCPServer.created_at).all()
    return [MCPServerOut.model_validate(s) for s in servers]


@router.get("/mcp-servers/{server_id}", response_model=MCPServerOut)
def get_mcp_server(server_id: str, _admin: AdminUser, db: DbSession) -> MCPServerOut:
    return MCPServerOut.model_validate(_get_server_or_404(server_id, db))


@router.patch("/mcp-servers/{server_id}", response_model=MCPServerOut)
async def patch_mcp_server(
    server_id: str, body: UpdateMCPServerRequest, _admin: AdminUser, db: DbSession
) -> MCPServerOut:
    server = _get_server_or_404(server_id, db)

    was_enabled = server.enabled

    if body.name is not None:
        server.name = body.name
    if body.url is not None:
        server.url = body.url
    if body.transport is not None:
        server.transport = body.transport
    if body.auth_type is not None:
        server.auth_type = body.auth_type
    if body.token is not None:
        server.token_encrypted = encrypt_token(body.token)

    flipping_enabled = (body.enabled is True) and not was_enabled

    if body.enabled is not None:
        server.enabled = body.enabled

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "name_taken", "An MCP server with that name already exists")

    if flipping_enabled:
        result = await probe_config(server)
        if not result.ok:
            db.rollback()
            raise ApiError(400, "probe_failed", result.error or "Probe failed")

    db.commit()
    db.refresh(server)
    return MCPServerOut.model_validate(server)


@router.delete("/mcp-servers/{server_id}", status_code=204)
def delete_mcp_server(server_id: str, _admin: AdminUser, db: DbSession) -> None:
    server = _get_server_or_404(server_id, db)
    db.delete(server)
    db.commit()


@router.post("/mcp-servers/{server_id}/test")
async def test_mcp_server(server_id: str, _admin: AdminUser, db: DbSession) -> dict:
    server = _get_server_or_404(server_id, db)
    result = await probe_config(server)
    return {"ok": result.ok, "tools": result.tools, "error": result.error}


# ---------------------------------------------------------------------------
# Group ↔ MCP server assignment
# ---------------------------------------------------------------------------


@router.put("/groups/{group_id}/mcp-servers", response_model=GroupDetailOut)
def set_group_mcp_servers(
    group_id: str, body: SetGroupServersRequest, _admin: AdminUser, db: DbSession
) -> GroupDetailOut:
    group = db.get(Group, group_id)
    if not group:
        raise ApiError(404, "not_found", "Group not found")

    servers: list[MCPServer] = []
    for sid in body.server_ids:
        server = db.get(MCPServer, sid)
        if not server:
            raise ApiError(400, "server_not_found", f"MCP server {sid!r} not found")
        servers.append(server)

    group.mcp_servers = servers
    db.commit()
    db.refresh(group)
    return GroupDetailOut.model_validate(group)
