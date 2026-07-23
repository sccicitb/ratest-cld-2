"""Tests for the detached ingest-job registry + startup reaper."""
from __future__ import annotations

import asyncio

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import KBFile, User
from app.rag.ingest_jobs import IngestJobRegistry, reap_stranded


@pytest.fixture()
def engine_factory(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return factory


class _FakeEmbedder:
    def embed_passages(self, texts):
        return [{"dense": [0.1] * 1024, "sparse": {"indices": [1], "values": [1.0]}} for _ in texts]

    def embed_query(self, text):
        return {"dense": [0.1] * 1024, "sparse": {"indices": [1], "values": [1.0]}}


def _make_file(db, storage_key, tmp_path, text="Hello world. " * 300) -> KBFile:
    (tmp_path / storage_key).write_text(text, encoding="utf-8")
    user = User(email=f"{storage_key}@t.com", display_name="U", password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    f = KBFile(
        user_id=user.id, scope="kb", session_id=None, name=storage_key, size=100,
        storage_key=storage_key, status="indexing", chunk_count=0, tags=[],
        is_public=True, group_id=None,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def test_task_survives_observer_cancellation(engine_factory, tmp_path):
    """The money test: an observer that stops mid-stream (client disconnect) must
    NOT kill the detached task — it runs to completion and the file lands ready."""
    qdrant = QdrantClient(":memory:")

    async def _run():
        db = engine_factory()
        file = _make_file(db, "doc.txt", tmp_path)
        reg = IngestJobRegistry(
            session_factory=engine_factory, client=qdrant, embedder=_FakeEmbedder(),
            max_concurrent=2,
        )
        job = reg.spawn(file.id)
        agen = reg.observe(file.id)
        await agen.__anext__()      # consume one progress event
        await agen.aclose()         # stop observing = client disconnect
        await job.task              # detached task must still finish
        db.expire_all()
        refreshed = db.get(KBFile, file.id)
        return refreshed.status, refreshed.chunk_count

    status, chunks = asyncio.run(_run())
    assert status == "ready"
    assert chunks > 0


def test_observe_yields_progress_then_terminates(engine_factory, tmp_path):
    qdrant = QdrantClient(":memory:")

    async def _run():
        db = engine_factory()
        file = _make_file(db, "doc.txt", tmp_path)
        reg = IngestJobRegistry(
            session_factory=engine_factory, client=qdrant, embedder=_FakeEmbedder(),
            max_concurrent=2,
        )
        reg.spawn(file.id)
        events = [ev async for ev in reg.observe(file.id)]
        await asyncio.sleep(0)  # let the done-callback run
        return events, file.id in reg._jobs

    events, still_registered = asyncio.run(_run())
    assert events and all(ev["type"] == "chunk_progress" for ev in events)
    assert still_registered is False  # evicted on completion


def test_reap_stranded_marks_indexing_error(engine_factory):
    db = engine_factory()
    u = User(email="r@t.com", display_name="U", password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    ids = {}
    for name, st in [("a", "indexing"), ("b", "indexing"), ("c", "ready")]:
        f = KBFile(user_id=u.id, scope="kb", session_id=None, name=name, size=1,
                   storage_key=name, status=st, chunk_count=(5 if st == "ready" else 0),
                   tags=[], is_public=True, group_id=None)
        db.add(f); db.commit(); db.refresh(f)
        ids[name] = f.id

    n = reap_stranded(db)

    assert n == 2
    assert db.get(KBFile, ids["a"]).status == "error"
    assert db.get(KBFile, ids["b"]).status == "error"
    assert db.get(KBFile, ids["c"]).status == "ready"  # untouched
