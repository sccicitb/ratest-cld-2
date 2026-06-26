"""Tests for sse() (Task 3.1) and blob storage save/open (Task 3.1)."""
from __future__ import annotations

import io

from app.sse import sse
from app.storage import open_blob, save_upload


# --- Task 3.1 — sse() --------------------------------------------------------


def test_sse_formats_compact_json_event():
    assert sse({"a": 1}) == b'data: {"a": 1}\n\n'


def test_sse_returns_bytes():
    out = sse({"type": "done"})
    assert isinstance(out, bytes)
    assert out.startswith(b"data: ")
    assert out.endswith(b"\n\n")


# --- Task 3.1 — storage -------------------------------------------------------


class _FakeUpload:
    """Minimal stand-in for FastAPI's UploadFile for unit testing."""

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = io.BytesIO(content)

    def read(self) -> bytes:
        return self.file.read()


def test_save_upload_returns_key_and_size(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))
    content = b"hello world, this is a test blob"
    upload = _FakeUpload("notes.txt", content)

    storage_key, size = save_upload(upload)

    assert size == len(content)
    assert storage_key.endswith(".txt")


def test_save_upload_and_open_blob_round_trip(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))
    content = b"round trip bytes \x00\x01\x02"
    upload = _FakeUpload("data.bin", content)

    storage_key, size = save_upload(upload)
    assert size == len(content)

    with open_blob(storage_key) as f:
        assert f.read() == content


def test_save_upload_creates_blob_dir_if_missing(tmp_path, monkeypatch):
    from app.config import settings

    missing_dir = tmp_path / "nested" / "blobs"
    monkeypatch.setattr(settings, "blob_dir", str(missing_dir))
    upload = _FakeUpload("a.txt", b"x")

    storage_key, _ = save_upload(upload)

    assert missing_dir.exists()
    with open_blob(storage_key) as f:
        assert f.read() == b"x"
