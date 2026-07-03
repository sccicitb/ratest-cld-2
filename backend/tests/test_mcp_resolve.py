"""Unit tests for resolve_caller_mcp_tools (§M4b.2).

Uses in-memory SQLite (via pytest engine/session_factory fixtures from conftest)
and monkeypatches `_list_tools` to avoid real network calls.

Coverage:
- No groups → []
- Groups but no granted servers → []
- Groups + enabled granted server → tools returned (namespaced)
- Disabled server → skipped
- Server only granted to a different group → not returned
- Down server (exception from _list_tools) → isolated, other server still works
"""
from __future__ import annotations

import asyncio

from mcp import types as mcp_types

from app.mcp import resolve as mcp_resolve_module
from app.mcp.resolve import resolve_caller_mcp_tools
from app.models import Group, MCPServer, User
from app.auth.security import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db, email: str = "u@test.com") -> User:
    u = User(email=email, display_name="U", password_hash=hash_password("pw"))
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_group(db, name: str = "grp") -> Group:
    g = Group(name=name)
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def _make_server(db, name: str, enabled: bool = True) -> MCPServer:
    s = MCPServer(
        name=name,
        url=f"http://fake-{name}/mcp",
        transport="streamable-http",
        auth_type="none",
        enabled=enabled,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _fake_tool(name: str) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description="A fake tool",
        inputSchema={"type": "object", "properties": {}},
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_groups_returns_empty(session_factory):
    db = session_factory()
    try:
        user = _make_user(db)

        async def _never_called(*_a, **_kw):
            raise AssertionError("_list_tools should not be called")

        import app.mcp.resolve as mod
        orig = mod._list_tools
        mod._list_tools = _never_called
        try:
            result = _run(resolve_caller_mcp_tools(db, user))
        finally:
            mod._list_tools = orig
    finally:
        db.close()

    assert result == []


def test_groups_but_no_granted_servers(session_factory):
    db = session_factory()
    try:
        user = _make_user(db)
        grp = _make_group(db)
        user.groups.append(grp)
        db.commit()
        # No MCPServer assigned to grp
        result = _run(resolve_caller_mcp_tools(db, user))
    finally:
        db.close()

    assert result == []


def test_enabled_granted_server_returns_namespaced_tools(session_factory, monkeypatch):
    db = session_factory()
    try:
        user = _make_user(db)
        grp = _make_group(db)
        user.groups.append(grp)
        srv = _make_server(db, "mysrv", enabled=True)
        grp.mcp_servers.append(srv)
        db.commit()

        async def _fake_list_tools(*, url, transport, headers, timeout):
            return [_fake_tool("dowork"), _fake_tool("domore")]

        monkeypatch.setattr(mcp_resolve_module, "_list_tools", _fake_list_tools)
        result = _run(resolve_caller_mcp_tools(db, user))
    finally:
        db.close()

    names = {t.name for t in result}
    assert "mysrv.dowork" in names
    assert "mysrv.domore" in names


def test_disabled_server_is_skipped(session_factory, monkeypatch):
    db = session_factory()
    try:
        user = _make_user(db)
        grp = _make_group(db)
        user.groups.append(grp)
        srv = _make_server(db, "offsrv", enabled=False)
        grp.mcp_servers.append(srv)
        db.commit()

        called = []

        async def _fake_list_tools(**_):
            called.append(True)
            return [_fake_tool("tool")]

        monkeypatch.setattr(mcp_resolve_module, "_list_tools", _fake_list_tools)
        result = _run(resolve_caller_mcp_tools(db, user))
    finally:
        db.close()

    assert result == []
    assert called == []  # disabled → never called


def test_server_granted_to_other_group_not_returned(session_factory, monkeypatch):
    db = session_factory()
    try:
        user = _make_user(db)
        grp_a = _make_group(db, "A")
        grp_b = _make_group(db, "B")
        user.groups.append(grp_a)
        # Only grp_b has the server
        srv = _make_server(db, "b_srv", enabled=True)
        grp_b.mcp_servers.append(srv)
        db.commit()

        async def _fake_list_tools(**_):
            return [_fake_tool("tool")]

        monkeypatch.setattr(mcp_resolve_module, "_list_tools", _fake_list_tools)
        result = _run(resolve_caller_mcp_tools(db, user))
    finally:
        db.close()

    assert result == []


def test_down_server_isolated_other_server_works(session_factory, monkeypatch):
    db = session_factory()
    try:
        user = _make_user(db)
        grp = _make_group(db)
        user.groups.append(grp)
        bad = _make_server(db, "bad", enabled=True)
        good = _make_server(db, "good", enabled=True)
        grp.mcp_servers.append(bad)
        grp.mcp_servers.append(good)
        db.commit()

        async def _fake_list_tools(*, url, **_):
            if "bad" in url:
                raise ConnectionError("refused")
            return [_fake_tool("ping")]

        monkeypatch.setattr(mcp_resolve_module, "_list_tools", _fake_list_tools)
        result = _run(resolve_caller_mcp_tools(db, user))
    finally:
        db.close()

    names = {t.name for t in result}
    assert "good.ping" in names
    assert not any(n.startswith("bad.") for n in names)


def test_multiple_groups_tools_merged(session_factory, monkeypatch):
    db = session_factory()
    try:
        user = _make_user(db)
        grp_a = _make_group(db, "AA")
        grp_b = _make_group(db, "BB")
        user.groups.append(grp_a)
        user.groups.append(grp_b)
        srv_a = _make_server(db, "srv_a", enabled=True)
        srv_b = _make_server(db, "srv_b", enabled=True)
        grp_a.mcp_servers.append(srv_a)
        grp_b.mcp_servers.append(srv_b)
        db.commit()

        async def _fake_list_tools(*, url, **_):
            if "srv_a" in url:
                return [_fake_tool("alpha")]
            return [_fake_tool("beta")]

        monkeypatch.setattr(mcp_resolve_module, "_list_tools", _fake_list_tools)
        result = _run(resolve_caller_mcp_tools(db, user))
    finally:
        db.close()

    names = {t.name for t in result}
    assert "srv_a.alpha" in names
    assert "srv_b.beta" in names
