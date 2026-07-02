"""Test fixtures — an isolated in-memory DB per test, wired into the app."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.security import hash_password
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import User


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection = one in-memory DB
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _plain_cookies():
    # TestClient runs over http; allow the refresh cookie to be stored.
    settings.cookie_secure = False
    yield
    settings.cookie_secure = True


@pytest.fixture()
def client(session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def demo_user(session_factory) -> dict:
    db = session_factory()
    user = User(
        email="demo@example.com",
        display_name="Alex Demo",
        password_hash=hash_password("demo1234"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    out = {"id": user.id, "email": user.email, "password": "demo1234"}
    db.close()
    return out


@pytest.fixture()
def auth_headers(client, demo_user) -> dict:
    r = client.post(
        "/api/auth/login",
        json={"email": demo_user["email"], "password": demo_user["password"]},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


# --- Admin fixtures (§M1) ---


@pytest.fixture()
def admin_user(session_factory) -> dict:
    db = session_factory()
    user = User(
        email="admin@example.com",
        display_name="Admin User",
        password_hash=hash_password("adminpass1"),
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    out = {"id": user.id, "email": user.email, "password": "adminpass1"}
    db.close()
    return out


@pytest.fixture()
def admin_headers(client, admin_user) -> dict:
    r = client.post(
        "/api/auth/login",
        json={"email": admin_user["email"], "password": admin_user["password"]},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}
