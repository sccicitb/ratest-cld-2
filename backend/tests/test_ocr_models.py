"""Tests for OCR runtime/model provisioning helpers."""
from __future__ import annotations

import os


def test_ensure_ort_dylib_sets_env(monkeypatch):
    monkeypatch.delenv("ORT_DYLIB_PATH", raising=False)
    from app.rag.ocr_models import ensure_ort_dylib

    path = ensure_ort_dylib()
    assert path is not None
    assert path.endswith((".dylib", ".so")) or ".so." in path or ".dylib." in path
    assert os.environ["ORT_DYLIB_PATH"] == path


def test_ensure_ort_dylib_is_idempotent(monkeypatch):
    from app.rag.ocr_models import ensure_ort_dylib

    monkeypatch.setenv("ORT_DYLIB_PATH", "/preset/libonnxruntime.dylib")
    assert ensure_ort_dylib() == "/preset/libonnxruntime.dylib"
