"""Tests for app.rag.extract (Task 3.2): PyMuPDF/docx/text routing + OCR stub."""
from __future__ import annotations

import json

import pytest

from app.rag.extract import SUPPORTED_KB_TYPES, extract_text


@pytest.fixture()
def blob_dir(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "blob_dir", str(tmp_path))
    return tmp_path


def _write(blob_dir, name: str, content: bytes) -> str:
    path = blob_dir / name
    path.write_bytes(content)
    return name


def test_supported_kb_types_contains_expected_extensions():
    assert SUPPORTED_KB_TYPES == {".pdf", ".md", ".txt", ".docx", ".doc", ".csv", ".json"}


def test_extract_text_from_text_layer_pdf(blob_dir):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello extract test")
    pdf_path = blob_dir / "sample.pdf"
    doc.save(str(pdf_path))
    doc.close()

    text = extract_text("sample.pdf", "sample.pdf")
    assert "Hello extract test" in text


def test_extract_text_from_scanned_pdf_raises_not_implemented(blob_dir):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    # Draw a rectangle only — no text layer at all (simulates a scanned page).
    page.draw_rect(fitz.Rect(10, 10, 100, 100))
    pdf_path = blob_dir / "scanned.pdf"
    doc.save(str(pdf_path))
    doc.close()

    with pytest.raises(NotImplementedError):
        extract_text("scanned.pdf", "scanned.pdf")


def test_extract_text_from_docx(blob_dir):
    import docx

    document = docx.Document()
    document.add_paragraph("First paragraph.")
    document.add_paragraph("Second paragraph.")
    docx_path = blob_dir / "sample.docx"
    document.save(str(docx_path))

    text = extract_text("sample.docx", "sample.docx")
    assert "First paragraph." in text
    assert "Second paragraph." in text


def test_extract_text_from_txt(blob_dir):
    _write(blob_dir, "notes.txt", "plain text content".encode("utf-8"))
    assert extract_text("notes.txt", "notes.txt") == "plain text content"


def test_extract_text_from_md(blob_dir):
    _write(blob_dir, "readme.md", "# Heading\nbody".encode("utf-8"))
    assert extract_text("readme.md", "readme.md") == "# Heading\nbody"


def test_extract_text_from_csv(blob_dir):
    _write(blob_dir, "data.csv", "a,b\n1,2".encode("utf-8"))
    assert extract_text("data.csv", "data.csv") == "a,b\n1,2"


def test_extract_text_from_json(blob_dir):
    payload = json.dumps({"k": "v"})
    _write(blob_dir, "data.json", payload.encode("utf-8"))
    assert extract_text("data.json", "data.json") == payload


def test_extract_text_legacy_doc_raises_clear_error(blob_dir):
    _write(blob_dir, "legacy.doc", b"\xd0\xcf\x11\xe0fake-ole-header")
    with pytest.raises(Exception) as exc_info:
        extract_text("legacy.doc", "legacy.doc")
    assert "libreoffice" in str(exc_info.value).lower() or "not" in str(exc_info.value).lower()


def test_extract_text_unknown_extension_raises_value_error(blob_dir):
    _write(blob_dir, "image.png", b"\x89PNG fake")
    with pytest.raises(ValueError):
        extract_text("image.png", "image.png")
