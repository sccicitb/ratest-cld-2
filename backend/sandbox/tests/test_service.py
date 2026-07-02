"""Tests for service/containers.py and service/main.py.

All Docker calls are mocked — no real daemon required.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_docker(monkeypatch):
    """Replace docker.from_env() with a fully-mocked client."""
    mock_client = MagicMock()
    mock_client.networks.list.return_value = []  # no network exists yet

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.ports = {"8000/tcp": [{"HostPort": "54321"}]}
    mock_client.containers.run.return_value = mock_container

    monkeypatch.setattr(
        "sandbox.service.containers.docker.from_env",
        lambda: mock_client,
    )
    return mock_client, mock_container


@pytest.fixture()
def manager(mock_docker, monkeypatch):
    """ContainerManager with docker mocked; _wait_ready stubbed to skip HTTP."""
    from sandbox.service.containers import ContainerManager

    mgr = ContainerManager()

    def _fake_wait(container, **_kw):  # noqa: ARG001
        return "http://127.0.0.1:54321"

    monkeypatch.setattr(mgr, "_wait_ready", _fake_wait)
    return mgr


# ---------------------------------------------------------------------------
# ensure_network
# ---------------------------------------------------------------------------


def test_ensure_network_creates_when_missing(mock_docker):
    from sandbox.service.containers import ContainerManager

    mock_client, _ = mock_docker
    ContainerManager().ensure_network()
    mock_client.networks.create.assert_called_once()


def test_ensure_network_skips_when_exists(mock_docker):
    from sandbox.service.containers import ContainerManager

    mock_client, _ = mock_docker
    mock_client.networks.list.return_value = [MagicMock()]  # network already exists
    ContainerManager().ensure_network()
    mock_client.networks.create.assert_not_called()


# ---------------------------------------------------------------------------
# get_or_create — four walls asserted on containers.run kwargs
# ---------------------------------------------------------------------------


def test_get_or_create_applies_all_four_walls(manager, mock_docker):
    mock_client, _ = mock_docker
    manager.get_or_create("sess-1")

    kw = mock_client.containers.run.call_args[1]

    # Privilege wall — non-root + all caps dropped. `no-new-privileges` is
    # deliberately NOT set (breaks execve with EPERM on some kernel/runc combos;
    # redundant given non-root + cap_drop=ALL).
    assert kw["user"] == "sandbox"
    assert kw["cap_drop"] == ["ALL"]
    assert "security_opt" not in kw

    # Filesystem wall
    assert kw["read_only"] is True
    assert "/work" in kw["tmpfs"]
    assert "/tmp" in kw["tmpfs"]  # IPython needs a writable tmpdir; load-bearing

    # Resource wall
    assert "mem_limit" in kw
    assert "nano_cpus" in kw
    assert kw["pids_limit"] > 0

    # Network wall — port bound to loopback only
    assert "8000/tcp" in kw["ports"]
    assert kw["ports"]["8000/tcp"][0] == "127.0.0.1"
    # Also confirm the network name comes from settings
    from sandbox.service.config import settings
    assert kw["network"] == settings.net


def test_get_or_create_reuses_existing_container(manager, mock_docker):
    mock_client, _ = mock_docker
    manager.get_or_create("sess-1")
    manager.get_or_create("sess-1")
    mock_client.containers.run.assert_called_once()


def test_get_or_create_distinct_sessions_get_distinct_containers(manager, mock_docker):
    mock_client, _ = mock_docker
    manager.get_or_create("sess-a")
    manager.get_or_create("sess-b")
    assert mock_client.containers.run.call_count == 2


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_stops_and_removes_container(manager, mock_docker):
    _, mock_container = mock_docker
    manager.get_or_create("sess-1")
    manager.remove("sess-1")
    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()
    assert "sess-1" not in manager._boxes


def test_remove_noop_for_unknown_session(manager):
    manager.remove("nonexistent")  # must not raise


# ---------------------------------------------------------------------------
# reap_idle
# ---------------------------------------------------------------------------


def test_reap_idle_removes_stale_boxes(manager, mock_docker, monkeypatch):
    import sandbox.service.containers as c_mod

    monkeypatch.setattr(c_mod.settings, "idle_ttl", 1)
    manager.get_or_create("sess-stale")
    # Wind back last_used so it exceeds idle_ttl
    manager._boxes["sess-stale"].last_used = time.monotonic() - 9999
    manager.reap_idle()
    assert "sess-stale" not in manager._boxes


def test_reap_idle_keeps_active_boxes(manager, mock_docker, monkeypatch):
    import sandbox.service.containers as c_mod

    monkeypatch.setattr(c_mod.settings, "idle_ttl", 9999)
    manager.get_or_create("sess-active")
    manager.reap_idle()
    assert "sess-active" in manager._boxes


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def test_execute_posts_to_container_port(manager, mock_docker, monkeypatch):
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"stdout": "4\n", "error": None, "artifacts": []}
    mock_resp.raise_for_status = MagicMock()

    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, *, json: dict, timeout: float):  # noqa: ARG001
        calls.append((url, json))
        return mock_resp

    monkeypatch.setattr(httpx, "post", fake_post)
    result = manager.execute("sess-exec", "print(2+2)")

    assert result["stdout"] == "4\n"
    assert len(calls) == 1
    assert "54321" in calls[0][0]
    assert calls[0][1]["code"] == "print(2+2)"


# ---------------------------------------------------------------------------
# FastAPI route tests (ContainerManager mocked via patch)
# ---------------------------------------------------------------------------


@pytest.fixture()
def service_client(monkeypatch):
    """TestClient for the service app with ContainerManager fully mocked."""
    from fastapi.testclient import TestClient

    import sandbox.service.main as main_mod

    mock_mgr = MagicMock()
    mock_mgr.execute.return_value = {"stdout": "4\n", "error": None, "artifacts": []}

    with patch.object(main_mod, "ContainerManager", return_value=mock_mgr):
        with TestClient(main_mod.app) as client:
            yield client, mock_mgr


def test_route_health(service_client):
    client, _ = service_client
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_route_execute_returns_runner_json(service_client):
    client, mock_mgr = service_client
    r = client.post("/sessions/sess-1/execute", json={"code": "print(2+2)"})
    assert r.status_code == 200
    data = r.json()
    assert data["stdout"] == "4\n"
    mock_mgr.execute.assert_called_once_with("sess-1", "print(2+2)")


def test_route_delete_calls_remove(service_client):
    client, mock_mgr = service_client
    r = client.delete("/sessions/sess-1")
    assert r.status_code == 204
    mock_mgr.remove.assert_called_once_with("sess-1")
