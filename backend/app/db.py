"""Database engine, session factory, and the declarative Base.

SQLite for dev (WAL + foreign keys on); swap `DATABASE_URL` for Postgres/MySQL
later — nothing else changes (§9.1).
"""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    # Ensure the parent dir of the sqlite file exists (e.g. ./data/).
    path = settings.database_url.split("///", 1)[-1]
    if path and path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
    if _is_sqlite:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # concurrent reader + the worker
        cur.execute("PRAGMA foreign_keys=ON")  # enforce cascades
        cur.close()


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory() -> sessionmaker:
    """Dependency seam for code (e.g. background tasks) that needs to open its
    own short-lived session outside the request's `get_db` lifecycle.
    Overridable in tests alongside `get_db`, so background work lands in the
    same (test) engine as the request that scheduled it.
    """
    return SessionLocal
