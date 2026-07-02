"""Admin group-management tests (§M2)."""
from __future__ import annotations

import pytest

from app.auth.security import hash_password
from app.groups.service import group_ids_for
from app.models import Group, User


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def user_a(session_factory) -> dict:
    db = session_factory()
    user = User(
        email="user_a@example.com",
        display_name="User A",
        password_hash=hash_password("pass1234"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    out = {"id": user.id, "email": user.email}
    db.close()
    return out


@pytest.fixture()
def user_b(session_factory) -> dict:
    db = session_factory()
    user = User(
        email="user_b@example.com",
        display_name="User B",
        password_hash=hash_password("pass1234"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    out = {"id": user.id, "email": user.email}
    db.close()
    return out


def _create_group(client, admin_headers, name: str, default_tags: list[str] | None = None) -> dict:
    payload: dict = {"name": name}
    if default_tags is not None:
        payload["defaultTags"] = default_tags
    r = client.post("/api/admin/groups", json=payload, headers=admin_headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


def test_admin_guard_list_403(client, auth_headers):
    assert client.get("/api/admin/groups", headers=auth_headers).status_code == 403


def test_admin_guard_create_403(client, auth_headers):
    r = client.post("/api/admin/groups", json={"name": "x"}, headers=auth_headers)
    assert r.status_code == 403


def test_admin_guard_get_403(client, auth_headers):
    r = client.get("/api/admin/groups/fake-id", headers=auth_headers)
    assert r.status_code == 403


def test_admin_guard_patch_403(client, auth_headers):
    r = client.patch("/api/admin/groups/fake-id", json={"name": "y"}, headers=auth_headers)
    assert r.status_code == 403


def test_admin_guard_delete_403(client, auth_headers):
    assert client.delete("/api/admin/groups/fake-id", headers=auth_headers).status_code == 403


def test_admin_guard_members_403(client, auth_headers):
    r = client.put(
        "/api/admin/groups/fake-id/members",
        json={"userIds": []},
        headers=auth_headers,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_group_201(client, admin_headers):
    r = client.post(
        "/api/admin/groups",
        json={"name": "engineers", "defaultTags": ["python", "backend"]},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "engineers"
    assert body["defaultTags"] == ["python", "backend"]
    assert body["memberCount"] == 0
    assert "id" in body
    assert "createdAt" in body


def test_create_group_default_tags_empty(client, admin_headers):
    r = client.post("/api/admin/groups", json={"name": "empty"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    assert r.json()["defaultTags"] == []


def test_create_group_unique_name_409(client, admin_headers):
    _create_group(client, admin_headers, "duplicate")
    r = client.post("/api/admin/groups", json={"name": "duplicate"}, headers=admin_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "name_taken"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_groups_empty(client, admin_headers):
    r = client.get("/api/admin/groups", headers=admin_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_list_groups_member_count(client, admin_headers, user_a, user_b):
    g = _create_group(client, admin_headers, "team-alpha")

    # Add two members
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"], user_b["id"]]},
        headers=admin_headers,
    )

    r = client.get("/api/admin/groups", headers=admin_headers)
    assert r.status_code == 200
    groups = r.json()
    found = next(grp for grp in groups if grp["id"] == g["id"])
    assert found["memberCount"] == 2


# ---------------------------------------------------------------------------
# Get detail
# ---------------------------------------------------------------------------


def test_get_group_404(client, admin_headers):
    r = client.get("/api/admin/groups/nonexistent", headers=admin_headers)
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_get_group_detail_no_members(client, admin_headers):
    g = _create_group(client, admin_headers, "solo")
    r = client.get(f"/api/admin/groups/{g['id']}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "solo"
    assert body["members"] == []
    assert body["memberCount"] == 0


def test_get_group_detail_with_members(client, admin_headers, user_a, user_b):
    g = _create_group(client, admin_headers, "team-beta")
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"]]},
        headers=admin_headers,
    )
    r = client.get(f"/api/admin/groups/{g['id']}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["memberCount"] == 1
    assert len(body["members"]) == 1
    assert body["members"][0]["id"] == user_a["id"]


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


def test_patch_group_name(client, admin_headers):
    g = _create_group(client, admin_headers, "old-name")
    r = client.patch(
        f"/api/admin/groups/{g['id']}",
        json={"name": "new-name"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "new-name"


def test_patch_group_default_tags(client, admin_headers):
    g = _create_group(client, admin_headers, "tagtest", default_tags=["a"])
    r = client.patch(
        f"/api/admin/groups/{g['id']}",
        json={"defaultTags": ["x", "y", "z"]},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["defaultTags"] == ["x", "y", "z"]


def test_patch_group_404(client, admin_headers):
    r = client.patch("/api/admin/groups/nope", json={"name": "x"}, headers=admin_headers)
    assert r.status_code == 404


def test_patch_group_name_conflict_409(client, admin_headers):
    _create_group(client, admin_headers, "alpha")
    g2 = _create_group(client, admin_headers, "beta")
    r = client.patch(
        f"/api/admin/groups/{g2['id']}",
        json={"name": "alpha"},
        headers=admin_headers,
    )
    assert r.status_code == 409
    assert r.json()["code"] == "name_taken"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_group_204(client, admin_headers):
    g = _create_group(client, admin_headers, "to-delete")
    r = client.delete(f"/api/admin/groups/{g['id']}", headers=admin_headers)
    assert r.status_code == 204

    # Confirm it's gone
    r2 = client.get(f"/api/admin/groups/{g['id']}", headers=admin_headers)
    assert r2.status_code == 404


def test_delete_group_404(client, admin_headers):
    assert client.delete("/api/admin/groups/ghost", headers=admin_headers).status_code == 404


def test_delete_group_removes_membership(client, admin_headers, user_a, session_factory):
    """Deleting a group cascades — membership rows disappear."""
    g = _create_group(client, admin_headers, "cascade-test")
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"]]},
        headers=admin_headers,
    )
    client.delete(f"/api/admin/groups/{g['id']}", headers=admin_headers)

    # Verify user still exists but has no groups
    db = session_factory()
    u = db.get(User, user_a["id"])
    assert u is not None
    assert u.groups == []
    db.close()


def test_delete_user_removes_membership(client, admin_headers, user_a, session_factory):
    """Deleting a USER cascades the other way — the group loses that member,
    and no orphan user_groups rows remain. (There's no hard-delete user API —
    users are disabled, not deleted — so exercise the FK cascade directly.)"""
    g = _create_group(client, admin_headers, "user-cascade-test")
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"]]},
        headers=admin_headers,
    )
    # Hard-delete the user row (proves the user_groups FK cascade fires).
    db = session_factory()
    db.delete(db.get(User, user_a["id"]))
    db.commit()
    db.close()

    db = session_factory()
    grp = db.get(Group, g["id"])
    assert grp is not None
    assert [m.id for m in grp.members] == []  # member gone, no orphan
    db.close()


# ---------------------------------------------------------------------------
# Set members (PUT) — set-semantics
# ---------------------------------------------------------------------------


def test_put_members_set_exactly(client, admin_headers, user_a, user_b):
    g = _create_group(client, admin_headers, "set-test")
    r = client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"], user_b["id"]]},
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["memberCount"] == 2
    member_ids = {m["id"] for m in body["members"]}
    assert member_ids == {user_a["id"], user_b["id"]}


def test_put_members_replace(client, admin_headers, user_a, user_b):
    """PUT replaces the entire member list — set-semantics."""
    g = _create_group(client, admin_headers, "replace-test")
    # First set: user_a + user_b
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"], user_b["id"]]},
        headers=admin_headers,
    )
    # Replace: only user_b
    r = client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_b["id"]]},
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["memberCount"] == 1
    assert body["members"][0]["id"] == user_b["id"]


def test_put_members_clear(client, admin_headers, user_a):
    g = _create_group(client, admin_headers, "clear-test")
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"]]},
        headers=admin_headers,
    )
    r = client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": []},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["memberCount"] == 0


def test_put_members_unknown_user_id_400(client, admin_headers):
    g = _create_group(client, admin_headers, "error-test")
    r = client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": ["totally-fake-user-id"]},
        headers=admin_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "user_not_found"


def test_put_members_group_404(client, admin_headers, user_a):
    r = client.put(
        "/api/admin/groups/nonexistent/members",
        json={"userIds": [user_a["id"]]},
        headers=admin_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# UserOut.groupIds reflects membership
# ---------------------------------------------------------------------------


def test_user_out_group_ids_empty(client, admin_headers, demo_user):
    """A user with no group memberships has groupIds = []."""
    r = client.get("/api/admin/users", headers=admin_headers)
    assert r.status_code == 200
    users = r.json()
    user = next(u for u in users if u["id"] == demo_user["id"])
    assert user["groupIds"] == []


def test_user_out_group_ids_reflects_membership(client, admin_headers, user_a):
    g = _create_group(client, admin_headers, "gids-test")
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"]]},
        headers=admin_headers,
    )
    r = client.get("/api/admin/users", headers=admin_headers)
    users = r.json()
    ua = next(u for u in users if u["id"] == user_a["id"])
    assert g["id"] in ua["groupIds"]


def test_group_detail_members_have_group_ids(client, admin_headers, user_a):
    g = _create_group(client, admin_headers, "nested-gids")
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [user_a["id"]]},
        headers=admin_headers,
    )
    r = client.get(f"/api/admin/groups/{g['id']}", headers=admin_headers)
    member = r.json()["members"][0]
    assert g["id"] in member["groupIds"]


# ---------------------------------------------------------------------------
# group_ids_for helper
# ---------------------------------------------------------------------------


def test_group_ids_for_zero_groups(session_factory):
    db = session_factory()
    user = User(
        email="nogroups@example.com",
        display_name="No Groups",
        password_hash=hash_password("pass1234"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    assert group_ids_for(user) == []
    db.close()


def test_group_ids_for_one_group(session_factory):
    db = session_factory()
    user = User(
        email="onegroup@example.com",
        display_name="One Group",
        password_hash=hash_password("pass1234"),
    )
    g = Group(name="solo-grp", default_tags=[])
    db.add_all([user, g])
    db.commit()
    g.members.append(user)
    db.commit()
    db.refresh(user)
    ids = group_ids_for(user)
    assert len(ids) == 1
    assert ids[0] == g.id
    db.close()


def test_group_ids_for_many_groups(session_factory):
    db = session_factory()
    user = User(
        email="manygroups@example.com",
        display_name="Many Groups",
        password_hash=hash_password("pass1234"),
    )
    grps = [Group(name=f"grp-{i}", default_tags=[]) for i in range(3)]
    db.add(user)
    db.add_all(grps)
    db.commit()
    for g in grps:
        g.members.append(user)
    db.commit()
    db.refresh(user)
    ids = group_ids_for(user)
    assert len(ids) == 3
    assert set(ids) == {g.id for g in grps}
    db.close()


# ---------------------------------------------------------------------------
# Migration schema check
# ---------------------------------------------------------------------------


def test_migration_schema_has_groups_tables():
    """Verify that Base.metadata includes the groups + user_groups tables."""
    from app.db import Base

    assert "groups" in Base.metadata.tables
    assert "user_groups" in Base.metadata.tables
