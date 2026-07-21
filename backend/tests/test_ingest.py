"""Tests for app.rag.ingest (Task 3.4): extract -> chunk -> embed -> upsert -> status.

One test uses the REAL embedder (module-scoped, loads BGE-M3 once) against a
real text file to prove the full pipeline end-to-end. The error-path test uses
a fake embedder since it never reaches the embed step.
"""
from __future__ import annotations

import asyncio

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import KBFile, User
from app.rag.embedder import Embedder, get_embedder
from app.rag.ingest import ingest
from app.rag.vectors import search


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return get_embedder()


@pytest.fixture()
def qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    yield db
    db.close()


def _make_user(db) -> User:
    user = User(email="ingest@example.com", display_name="Ingest Tester", password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_kb_file(
    db,
    user_id: str,
    storage_key: str,
    name: str,
    *,
    is_public: bool = False,
) -> KBFile:
    file = KBFile(
        user_id=user_id,
        scope="kb",
        session_id=None,
        name=name,
        size=100,
        storage_key=storage_key,
        status="indexing",
        chunk_count=0,
        tags=["alpha"],
        is_public=is_public,
        group_id=None,
    )
    db.add(file)
    db.commit()
    db.refresh(file)
    return file


async def _collect(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


def test_ingest_real_pipeline_extracts_chunks_embeds_and_marks_ready(
    db_session, qdrant: QdrantClient, embedder: Embedder, tmp_path
):
    """Real extract -> chunk -> embed -> upsert pipeline on a real .txt file."""
    from app.config import settings

    storage_key = "doc.txt"
    text = "Cats are small domesticated carnivorous mammals. " * 50 + "\n\n" + (
        "Dogs are loyal companions and have been domesticated for millennia. " * 50
    )
    (tmp_path / storage_key).write_text(text, encoding="utf-8")
    assert settings.blob_dir == str(tmp_path)

    user = _make_user(db_session)
    # is_public=True so chunks are visible without group membership (M3)
    file = _make_kb_file(db_session, user.id, storage_key, "doc.txt", is_public=True)

    events = asyncio.run(
        _collect(ingest(db_session, file.id, client=qdrant, embedder=embedder))
    )

    assert len(events) >= 1
    for ev in events:
        assert ev["type"] == "chunk_progress"
        assert ev["fileName"] == "doc.txt"
    assert events[-1]["progress"] == 100

    db_session.refresh(file)
    assert file.status == "ready"
    assert file.chunk_count > 0

    results = search(
        qdrant, embedder, query="domesticated carnivorous mammals", user_id=user.id,
        session_id=None, caller_group_ids=[], k=5,
    )
    assert any(r["file_id"] == file.id for r in results)


class _FakeEmbedder:
    def embed_passages(self, texts):
        return [{"dense": [0.1] * 1024, "sparse": {"indices": [1], "values": [1.0]}} for _ in texts]

    def embed_query(self, text):
        return {"dense": [0.1] * 1024, "sparse": {"indices": [1], "values": [1.0]}}


class _FailingEmbedder:
    """Embedder that raises RuntimeError on embed_passages."""

    def embed_passages(self, texts):
        raise RuntimeError("embedder boom")

    def embed_query(self, text):
        return {"dense": [0.1] * 1024, "sparse": {"indices": [1], "values": [1.0]}}


def test_ingest_marks_error_on_extraction_failure(db_session, qdrant: QdrantClient, tmp_path):
    """Unsupported extension -> extract_text raises ValueError -> status=error."""
    storage_key = "scan.png"
    (tmp_path / storage_key).write_bytes(b"\x89PNG fake")
    user = _make_user(db_session)
    file = _make_kb_file(db_session, user.id, storage_key, "scan.png")

    fake = _FakeEmbedder()

    with pytest.raises(ValueError):
        asyncio.run(_collect(ingest(db_session, file.id, client=qdrant, embedder=fake)))

    db_session.refresh(file)
    assert file.status == "error"
    assert file.chunk_count == 0


def test_ingest_marks_error_on_embed_failure(db_session, qdrant: QdrantClient, tmp_path):
    """Embedder failure during upsert phase -> status=error (not left at indexing)."""
    storage_key = "doc.txt"
    text = "Hello world. " * 100
    (tmp_path / storage_key).write_text(text, encoding="utf-8")

    user = _make_user(db_session)
    file = _make_kb_file(db_session, user.id, storage_key, "doc.txt")

    failing = _FailingEmbedder()

    with pytest.raises(RuntimeError, match="embedder boom"):
        asyncio.run(_collect(ingest(db_session, file.id, client=qdrant, embedder=failing)))

    db_session.refresh(file)
    assert file.status == "error", f"Expected status='error', got '{file.status}'"
    assert file.chunk_count == 0


def test_ingest_marks_error_when_cancelled_midway(db_session, qdrant: QdrantClient, tmp_path):
    """Client disconnect (refresh / network loss) cancels the streaming ingest
    generator with asyncio.CancelledError — a BaseException, so it slips past
    ingest's `except Exception`. The file must NOT be left stranded at
    'indexing' (visible in the UI, no/partial chunks, never retrievable); an
    interrupted ingest should finalize to 'error' like every other failure.
    """
    storage_key = "doc.txt"
    text = "Hello world. " * 500  # several chunks -> at least one embed batch + yield
    (tmp_path / storage_key).write_text(text, encoding="utf-8")

    user = _make_user(db_session)
    file = _make_kb_file(db_session, user.id, storage_key, "doc.txt")

    fake = _FakeEmbedder()

    async def _drive_then_cancel():
        gen = ingest(db_session, file.id, client=qdrant, embedder=fake)
        await gen.__anext__()  # run through the first batch; suspend at the yield
        # Simulate Starlette cancelling the response generator on disconnect.
        with pytest.raises(asyncio.CancelledError):
            await gen.athrow(asyncio.CancelledError())

    asyncio.run(_drive_then_cancel())

    db_session.refresh(file)
    assert file.status == "error", (
        f"Interrupted ingest left status='{file.status}', expected 'error' "
        "(orphaned 'indexing' file: shows in UI but has no knowledge)"
    )


def test_ingest_marks_error_on_ocr_failure(db_session, qdrant: QdrantClient, tmp_path, monkeypatch):
    """Surya OCR failure on scanned PDF -> status=error (not left at indexing)."""
    import fitz

    # Create a thin/scanned PDF: a page with no text layer
    # so extraction will route to OCR.
    storage_key = "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Just add a rectangle (no text), so PyMuPDF extract gets empty text,
    # making it "thin" and triggering OCR.
    page.draw_rect(fitz.Rect(50, 50, 200, 200), color=(0, 0, 0), fill=(1, 1, 1))
    pdf_bytes = doc.write()
    doc.close()

    (tmp_path / storage_key).write_bytes(pdf_bytes)

    user = _make_user(db_session)
    file = _make_kb_file(db_session, user.id, storage_key, "scanned.pdf")

    # Monkeypatch ocr_images to raise instead of loading real Surya.
    def failing_ocr(images):
        raise RuntimeError("OCR boom")

    monkeypatch.setattr("app.rag.ocr.ocr_images", failing_ocr)

    fake = _FakeEmbedder()

    with pytest.raises(RuntimeError, match="OCR boom"):
        asyncio.run(_collect(ingest(db_session, file.id, client=qdrant, embedder=fake)))

    db_session.refresh(file)
    assert file.status == "error", f"Expected status='error', got '{file.status}'"
    assert file.chunk_count == 0
