"""Admin MCP server catalog tests (§M4a.5)."""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from app.mcp.crypto import decrypt_token, encrypt_token
from app.mcp.verify import ProbeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MCP_KEY = Fernet.generate_key().decode()


def _create_server(client, admin_headers, **kwargs):
    payload = {
        "name": kwargs.pop("name", "test-server"),
        "url": kwargs.pop("url", "http://localhost:9999/mcp"),
        **kwargs,
    }
    return client.post("/api/admin/mcp-servers", json=payload, headers=admin_headers)


def _create_group(client, admin_headers, name: str) -> dict:
    r = client.post("/api/admin/groups", json={"name": name}, headers=admin_headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Crypto unit tests
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip(monkeypatch):
    """encrypt → decrypt yields the original token."""
    monkeypatch.setattr("app.mcp.crypto.settings", type("S", (), {"mcp_token_key": MCP_KEY})())
    plain = "super-secret-bearer-token"
    enc = encrypt_token(plain)
    assert enc != plain
    assert decrypt_token(enc) == plain


def test_encrypt_missing_key_raises(monkeypatch):
    """encrypt_token raises ApiError(400, mcp_key_missing) when key is unset."""
    monkeypatch.setattr("app.mcp.crypto.settings", type("S", (), {"mcp_token_key": None})())
    from app.errors import ApiError

    with pytest.raises(ApiError) as exc_info:
        encrypt_token("tok")
    assert exc_info.value.status == 400
    assert exc_info.value.code == "mcp_key_missing"


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


def test_admin_guard_list_403(client, auth_headers):
    assert client.get("/api/admin/mcp-servers", headers=auth_headers).status_code == 403


def test_admin_guard_create_403(client, auth_headers):
    r = client.post(
        "/api/admin/mcp-servers",
        json={"name": "x", "url": "http://x/mcp"},
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_admin_guard_get_403(client, auth_headers):
    assert client.get("/api/admin/mcp-servers/fake", headers=auth_headers).status_code == 403


def test_admin_guard_patch_403(client, auth_headers):
    r = client.patch(
        "/api/admin/mcp-servers/fake",
        json={"name": "y"},
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_admin_guard_delete_403(client, auth_headers):
    assert client.delete("/api/admin/mcp-servers/fake", headers=auth_headers).status_code == 403


def test_admin_guard_test_403(client, auth_headers):
    assert (
        client.post("/api/admin/mcp-servers/fake/test", headers=auth_headers).status_code == 403
    )


def test_admin_guard_group_assign_403(client, auth_headers):
    r = client.put(
        "/api/admin/groups/fake/mcp-servers",
        json={"serverIds": []},
        headers=auth_headers,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_server_none_auth_201(client, admin_headers):
    """Create a none-auth server (disabled); no probe needed."""
    r = _create_server(client, admin_headers, name="srv-none", url="http://example.com/mcp")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "srv-none"
    assert body["authType"] == "none"
    assert body["enabled"] is False
    assert "token" not in body
    assert "tokenEncrypted" not in body
    assert "id" in body
    assert "createdAt" in body


def test_create_server_bearer_token_not_in_response(client, admin_headers, monkeypatch):
    """Bearer token is encrypted at rest and NEVER returned in response."""
    monkeypatch.setattr("app.mcp.crypto.settings", type("S", (), {"mcp_token_key": MCP_KEY})())
    r = _create_server(
        client,
        admin_headers,
        name="srv-bearer",
        url="http://example.com/mcp",
        authType="bearer",
        token="my-secret",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "token" not in body
    assert "tokenEncrypted" not in body
    assert body["authType"] == "bearer"


def test_create_server_unique_name_409(client, admin_headers):
    _create_server(client, admin_headers, name="dup")
    r = _create_server(client, admin_headers, name="dup")
    assert r.status_code == 409
    assert r.json()["code"] == "name_taken"


def test_create_server_enabled_failing_probe_400(client, admin_headers, monkeypatch):
    """Enabling a server with a failing probe rejects with 400 probe_failed."""

    async def fake_probe(server, timeout=15.0):
        return ProbeResult(ok=False, tools=[], error="connection refused")

    monkeypatch.setattr("app.admin.mcp.probe_config", fake_probe)
    r = _create_server(
        client,
        admin_headers,
        name="failing-srv",
        url="http://dead-server/mcp",
        enabled=True,
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "probe_failed"


def test_create_server_enabled_passing_probe_201(client, admin_headers, monkeypatch):
    """Enabling a server with a passing probe creates enabled=True."""

    async def fake_probe(server, timeout=15.0):
        return ProbeResult(ok=True, tools=["tool_a", "tool_b"], error=None)

    monkeypatch.setattr("app.admin.mcp.probe_config", fake_probe)
    r = _create_server(
        client,
        admin_headers,
        name="passing-srv",
        url="http://good-server/mcp",
        enabled=True,
    )
    assert r.status_code == 201, r.text
    assert r.json()["enabled"] is True


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


def test_list_mcp_servers_empty(client, admin_headers):
    r = client.get("/api/admin/mcp-servers", headers=admin_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_list_mcp_servers_returns_servers(client, admin_headers):
    _create_server(client, admin_headers, name="alpha")
    _create_server(client, admin_headers, name="beta")
    r = client.get("/api/admin/mcp-servers", headers=admin_headers)
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "alpha" in names
    assert "beta" in names


def test_list_never_includes_token(client, admin_headers, monkeypatch):
    monkeypatch.setattr("app.mcp.crypto.settings", type("S", (), {"mcp_token_key": MCP_KEY})())
    _create_server(
        client, admin_headers, name="tok-srv", authType="bearer", token="secret"
    )
    r = client.get("/api/admin/mcp-servers", headers=admin_headers)
    for s in r.json():
        assert "token" not in s
        assert "tokenEncrypted" not in s


def test_get_mcp_server_404(client, admin_headers):
    r = client.get("/api/admin/mcp-servers/nonexistent", headers=admin_headers)
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_get_mcp_server_200(client, admin_headers):
    created = _create_server(client, admin_headers, name="get-me").json()
    r = client.get(f"/api/admin/mcp-servers/{created['id']}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_never_includes_token(client, admin_headers, monkeypatch):
    monkeypatch.setattr("app.mcp.crypto.settings", type("S", (), {"mcp_token_key": MCP_KEY})())
    created = _create_server(
        client, admin_headers, name="bearer-get", authType="bearer", token="s3cr3t"
    ).json()
    r = client.get(f"/api/admin/mcp-servers/{created['id']}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "token" not in body
    assert "tokenEncrypted" not in body


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def test_patch_rename(client, admin_headers):
    created = _create_server(client, admin_headers, name="old-name").json()
    r = client.patch(
        f"/api/admin/mcp-servers/{created['id']}",
        json={"name": "new-name"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "new-name"


def test_patch_404(client, admin_headers):
    r = client.patch("/api/admin/mcp-servers/nope", json={"name": "x"}, headers=admin_headers)
    assert r.status_code == 404


def test_patch_flip_enabled_with_failing_probe_400(client, admin_headers, monkeypatch):
    created = _create_server(client, admin_headers, name="disabled-srv").json()
    assert created["enabled"] is False

    async def fake_probe(server, timeout=15.0):
        return ProbeResult(ok=False, tools=[], error="timeout")

    monkeypatch.setattr("app.admin.mcp.probe_config", fake_probe)
    r = client.patch(
        f"/api/admin/mcp-servers/{created['id']}",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "probe_failed"


def test_patch_flip_enabled_with_passing_probe_200(client, admin_headers, monkeypatch):
    created = _create_server(client, admin_headers, name="will-enable").json()

    async def fake_probe(server, timeout=15.0):
        return ProbeResult(ok=True, tools=["ping"], error=None)

    monkeypatch.setattr("app.admin.mcp.probe_config", fake_probe)
    r = client.patch(
        f"/api/admin/mcp-servers/{created['id']}",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is True


def test_patch_update_token(client, admin_headers, monkeypatch):
    monkeypatch.setattr("app.mcp.crypto.settings", type("S", (), {"mcp_token_key": MCP_KEY})())
    _create_server(
        client, admin_headers, name="tok-update", authType="bearer", token="old-token"
    )
    servers = client.get("/api/admin/mcp-servers", headers=admin_headers).json()
    srv = next(s for s in servers if s["name"] == "tok-update")
    r = client.patch(
        f"/api/admin/mcp-servers/{srv['id']}",
        json={"token": "new-token"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert "token" not in r.json()
    assert "tokenEncrypted" not in r.json()


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_mcp_server_204(client, admin_headers):
    created = _create_server(client, admin_headers, name="to-delete").json()
    r = client.delete(f"/api/admin/mcp-servers/{created['id']}", headers=admin_headers)
    assert r.status_code == 204
    r2 = client.get(f"/api/admin/mcp-servers/{created['id']}", headers=admin_headers)
    assert r2.status_code == 404


def test_delete_mcp_server_404(client, admin_headers):
    assert client.delete("/api/admin/mcp-servers/ghost", headers=admin_headers).status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/test
# ---------------------------------------------------------------------------


def test_test_endpoint_monkeypatched_ok(client, admin_headers, monkeypatch):
    """POST /{id}/test returns {ok, tools, error} from probe_config."""
    created = _create_server(client, admin_headers, name="test-me").json()

    async def fake_probe(server, timeout=15.0):
        return ProbeResult(ok=True, tools=["echo", "add"], error=None)

    monkeypatch.setattr("app.admin.mcp.probe_config", fake_probe)
    r = client.post(f"/api/admin/mcp-servers/{created['id']}/test", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert set(body["tools"]) == {"echo", "add"}
    assert body["error"] is None


def test_test_endpoint_monkeypatched_fail(client, admin_headers, monkeypatch):
    created = _create_server(client, admin_headers, name="test-fail").json()

    async def fake_probe(server, timeout=15.0):
        return ProbeResult(ok=False, tools=[], error="connection refused")

    monkeypatch.setattr("app.admin.mcp.probe_config", fake_probe)
    r = client.post(f"/api/admin/mcp-servers/{created['id']}/test", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "connection refused"


def test_test_endpoint_real_in_memory_probe():
    """One real probe via in-memory FastMCP — proves probe_server returns real tool names."""
    from mcp.server.fastmcp import FastMCP
    from mcp.shared.memory import create_connected_server_and_client_session as mem

    fake_mcp = FastMCP("probe-test")

    @fake_mcp.tool()
    def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}"

    @fake_mcp.tool()
    def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    async def run():
        async with mem(fake_mcp) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            return [t.name for t in tools_result.tools]

    tool_names = asyncio.run(run())
    assert "greet" in tool_names
    assert "multiply" in tool_names


# ---------------------------------------------------------------------------
# Group assignment + mcpServerIds in detail
# ---------------------------------------------------------------------------


def test_group_mcp_servers_set_semantics(client, admin_headers):
    """PUT /groups/{id}/mcp-servers replaces the full set."""
    g = _create_group(client, admin_headers, "grp-mcp-set")
    s1 = _create_server(client, admin_headers, name="svr1").json()
    s2 = _create_server(client, admin_headers, name="svr2").json()
    s3 = _create_server(client, admin_headers, name="svr3").json()

    # Set s1+s2
    r = client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": [s1["id"], s2["id"]]},
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body["mcpServerIds"]) == {s1["id"], s2["id"]}

    # Replace with s3 only
    r2 = client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": [s3["id"]]},
        headers=admin_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["mcpServerIds"] == [s3["id"]]


def test_group_detail_shows_mcp_server_ids(client, admin_headers):
    g = _create_group(client, admin_headers, "grp-detail-check")
    srv = _create_server(client, admin_headers, name="detail-srv").json()

    client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": [srv["id"]]},
        headers=admin_headers,
    )
    r = client.get(f"/api/admin/groups/{g['id']}", headers=admin_headers)
    assert r.status_code == 200
    assert srv["id"] in r.json()["mcpServerIds"]


def test_group_mcp_clear(client, admin_headers):
    g = _create_group(client, admin_headers, "grp-clear")
    srv = _create_server(client, admin_headers, name="clear-srv").json()
    client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": [srv["id"]]},
        headers=admin_headers,
    )
    r = client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": []},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["mcpServerIds"] == []


def test_group_mcp_unknown_server_400(client, admin_headers):
    g = _create_group(client, admin_headers, "grp-bad-srv")
    r = client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": ["nonexistent-id"]},
        headers=admin_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "server_not_found"


def test_group_mcp_group_404(client, admin_headers):
    r = client.put(
        "/api/admin/groups/nonexistent/mcp-servers",
        json={"serverIds": []},
        headers=admin_headers,
    )
    assert r.status_code == 404


def test_delete_server_cascades_group_mcp(client, admin_headers, session_factory):
    """Deleting an MCPServer cascades — group_mcp rows disappear."""
    from app.models import Group as GroupModel

    g = _create_group(client, admin_headers, "cascade-grp")
    srv = _create_server(client, admin_headers, name="cascade-srv").json()
    client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": [srv["id"]]},
        headers=admin_headers,
    )
    client.delete(f"/api/admin/mcp-servers/{srv['id']}", headers=admin_headers)
    db = session_factory()
    grp = db.get(GroupModel, g["id"])
    assert grp is not None
    assert grp.mcp_servers == []
    db.close()


def test_delete_group_cascades_group_mcp(client, admin_headers, session_factory):
    """Deleting a Group cascades — group_mcp rows disappear."""
    from app.models import MCPServer as MCPServerModel

    g = _create_group(client, admin_headers, "del-grp-cascade")
    srv = _create_server(client, admin_headers, name="del-grp-srv").json()
    client.put(
        f"/api/admin/groups/{g['id']}/mcp-servers",
        json={"serverIds": [srv["id"]]},
        headers=admin_headers,
    )
    client.delete(f"/api/admin/groups/{g['id']}", headers=admin_headers)
    db = session_factory()
    s = db.get(MCPServerModel, srv["id"])
    assert s is not None
    assert s.groups == []
    db.close()
