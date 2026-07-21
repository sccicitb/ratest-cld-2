"""Document text extraction (§8.1): PDFOxide + python-docx + plain text.

Routing is by filename extension. PDFs go through PDFOxide: each page is read
with `extract_text_auto`, which returns the native text layer and transparently
OCRs (PaddleOCR-v4 ONNX) any scanned/image-only page — no separate OCR module.
PDFOxide is stricter than PyMuPDF about malformed files, so a broken PDF is
repaired once via PyMuPDF before opening (PyMuPDF is kept solely for this).
`.doc` (legacy binary Word) needs a LibreOffice conversion step that isn't
wired here, so it raises a clear, actionable error.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.storage import open_blob

SUPPORTED_KB_TYPES = {".pdf", ".md", ".txt", ".docx", ".doc", ".csv", ".json"}

_TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".json"}


def _open_pdf_oxide(data: bytes):
    """Open PDF bytes with PDFOxide, repairing a malformed file via PyMuPDF.

    PDFOxide rejects some PDFs PyMuPDF tolerates (e.g. "Invalid cross-reference
    table"). On failure, round-trip the bytes through PyMuPDF's repair (garbage
    collect + clean) and retry once.
    """
    import pdf_oxide

    try:
        return pdf_oxide.PdfDocument.from_bytes(data)
    except Exception:
        import fitz

        clean = fitz.open(stream=data, filetype="pdf").tobytes(
            garbage=4, clean=True, deflate=True
        )
        return pdf_oxide.PdfDocument.from_bytes(clean)


def _extract_pdf(storage_key: str) -> str:
    with open_blob(storage_key) as f:
        data = f.read()

    doc = _open_pdf_oxide(data)
    # extract_text_auto: native text, transparently OCRing scanned pages.
    # extract_text: native only (used when OCR is disabled).
    read = doc.extract_text_auto if settings.ocr_enabled else doc.extract_text
    return "\n".join(read(pg) for pg in range(doc.page_count()))


def _extract_docx(storage_key: str) -> str:
    import docx

    with open_blob(storage_key) as f:
        document = docx.Document(f)
    return "\n".join(p.text for p in document.paragraphs)


def _extract_plain_text(storage_key: str) -> str:
    with open_blob(storage_key) as f:
        return f.read().decode("utf-8")


def extract_text(storage_key: str, filename: str) -> str:
    """Extract plain text from a stored blob, routing on `filename`'s extension."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _extract_pdf(storage_key)
    if ext == ".docx":
        return _extract_docx(storage_key)
    if ext in _TEXT_EXTENSIONS:
        return _extract_plain_text(storage_key)
    if ext == ".doc":
        raise ValueError(
            "Legacy .doc files are not supported directly — convert to .docx "
            "via LibreOffice first (not wired in this build)."
        )
    raise ValueError(f"Unsupported file type: {ext!r}")
