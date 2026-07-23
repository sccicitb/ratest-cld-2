"""Real-OCR integration test (spec §6): proves PaddleOCR-ONNX actually
recognizes text on a scanned (image-only) PDF page via PDFOxide.

This is deliberately NOT a routing/monkeypatch test (see tests/test_extract.py
for those) — it exercises the real onnxruntime + PaddleOCR models end to end.
Skips cleanly when either isn't provisioned, mirroring the Docker-availability
skip pattern in sandbox/tests/test_integration.py.
"""
from __future__ import annotations

import glob
import os

import pytest

try:
    import onnxruntime  # noqa: F401

    _HAS_ORT = True
except ImportError:
    _HAS_ORT = False


def _models_dir() -> str:
    return os.environ.get("PDF_OXIDE_MODEL_DIR") or os.path.expanduser(
        "~/.cache/pdf_oxide/models"
    )


def _has_models() -> bool:
    return bool(glob.glob(os.path.join(_models_dir(), "*.onnx")))


if not _HAS_ORT or not _has_models():
    pytest.skip(
        "PDFOxide OCR models not provisioned — run scripts/setup_ocr_models.py",
        allow_module_level=True,
    )


@pytest.fixture()
def blob_dir(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))
    return tmp_path


def _write(blob_dir, name: str, content: bytes) -> str:
    path = blob_dir / name
    path.write_bytes(content)
    return name


def _make_scanned_pdf_bytes() -> bytes:
    """A single-page PDF containing ONLY a rasterized image of text — no
    text layer at all, so native extraction must return nothing."""
    import io

    import fitz
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.text((40, 100), "OCRPROOF", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=800, height=300)
    page.insert_image(page.rect, stream=png_bytes)
    out = doc.write()
    doc.close()
    return out


def test_ocr_recognizes_text_on_image_only_pdf_page(blob_dir, monkeypatch):
    from app.config import settings
    from app.rag.ocr_models import ensure_ort_dylib

    monkeypatch.setattr(settings, "ocr_enabled", True)
    assert ensure_ort_dylib() is not None, "onnxruntime dylib should be found"

    import pdf_oxide

    data = _make_scanned_pdf_bytes()
    _write(blob_dir, "scanned.pdf", data)

    doc = pdf_oxide.PdfDocument.from_bytes(data)

    native = doc.extract_text(0)
    assert native.strip() == "", (
        f"expected no native text on an image-only page, got: {native!r}"
    )

    ocr_text = doc.extract_text_auto(0)
    assert ocr_text.strip() != "", "OCR should have recognized SOME text"
    assert any(c.isalnum() for c in ocr_text), (
        f"OCR output should contain alphanumeric characters, got: {ocr_text!r}"
    )
