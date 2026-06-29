"""Hermetic tests for Stage-11 SPA-serving behaviour.

These tests create a minimal fresh FastAPI app (not the production singleton)
and call _mount_spa() after pointing settings.spa_dir at a fake build dir in
tmp_path.  No real frontend build is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.main import _mount_spa


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def spa_dir(tmp_path: Path) -> Path:
    """Minimal fake SPA build dir: index.html + assets/app.js."""
    (tmp_path / "index.html").write_text("<html><body>SPA</body></html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('hello')")
    return tmp_path


def _make_spa_app(spa_path: Path) -> FastAPI:
    """Build a fresh FastAPI app with SPA serving wired up."""
    test_app = FastAPI()

    @test_app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Temporarily point settings at the fake build dir so _mount_spa sees it.
    original = settings.spa_dir
    settings.spa_dir = str(spa_path)
    try:
        _mount_spa(test_app)
    finally:
        settings.spa_dir = original  # restore for other tests

    return test_app


@pytest.fixture()
def spa_client(spa_dir: Path) -> TestClient:
    return TestClient(_make_spa_app(spa_dir), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# SPA serving — spa_dir set
# ---------------------------------------------------------------------------

def test_root_returns_index_html(spa_client: TestClient) -> None:
    r = spa_client.get("/")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_client_route_falls_back_to_index_html(spa_client: TestClient) -> None:
    r = spa_client.get("/some/client/route")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_assets_are_served(spa_client: TestClient) -> None:
    r = spa_client.get("/assets/app.js")
    assert r.status_code == 200
    assert "hello" in r.text


def test_api_health_not_shadowed(spa_client: TestClient) -> None:
    r = spa_client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_unknown_returns_json_404_not_index_html(spa_client: TestClient) -> None:
    r = spa_client.get("/api/nope")
    assert r.status_code == 404
    content_type = r.headers.get("content-type", "")
    # Must be JSON (FastAPI default HTTPException handler), never HTML.
    assert "text/html" not in content_type
    assert r.json()["detail"] == "Not found"


# ---------------------------------------------------------------------------
# Dev mode — spa_dir=None leaves / unrouted (dev Vite proxy is used instead)
# ---------------------------------------------------------------------------

def test_dev_mode_root_is_not_served() -> None:
    dev_app = FastAPI()

    original = settings.spa_dir
    settings.spa_dir = None
    try:
        _mount_spa(dev_app)  # should be a no-op
    finally:
        settings.spa_dir = original

    with TestClient(dev_app, raise_server_exceptions=True) as client:
        r = client.get("/")
        assert r.status_code == 404  # no catch-all registered in dev mode
