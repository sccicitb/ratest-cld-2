"""Local blob storage for raw uploaded files (§8.1, §8.3).

Files land under `settings.blob_dir` keyed by an opaque uuid + original
extension. The DB/Qdrant only ever reference the `storage_key`; this module
is the one place that knows the on-disk layout.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import BinaryIO, Protocol


class _UploadLike(Protocol):
    filename: str | None

    def read(self) -> bytes:
        ...


def _blob_dir() -> Path:
    from app.config import settings

    path = Path(settings.blob_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(file: _UploadLike) -> tuple[str, int]:
    """Persist an uploaded file's bytes to the blob dir.

    Returns (storage_key, size_in_bytes). `storage_key` is an opaque id
    (uuid4 + original extension) suitable for later `open_blob` lookups.
    """
    suffix = Path(file.filename or "").suffix
    storage_key = f"{uuid.uuid4().hex}{suffix}"
    content = file.read()

    dest = _blob_dir() / storage_key
    dest.write_bytes(content)

    return storage_key, len(content)


def open_blob(storage_key: str) -> BinaryIO:
    """Open a stored blob for reading, by its storage_key."""
    path = _blob_dir() / storage_key
    return open(path, "rb")
