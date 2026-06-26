"""Document text extraction (§8.1): PyMuPDF + python-docx + plain text.

Routing is by filename extension. PDFs go through PyMuPDF; if the extracted
text is empty/whitespace-only (a scanned/image-only PDF with no text layer),
we route to `_ocr()` — currently a stub, since Surya OCR is a deferred task
(Task 3.8). `.doc` (legacy binary Word) needs a LibreOffice conversion step
that isn't wired here, so it raises a clear, actionable error.
"""
from __future__ import annotations

from pathlib import Path

from app.storage import open_blob

SUPPORTED_KB_TYPES = {".pdf", ".md", ".txt", ".docx", ".doc", ".csv", ".json"}

_TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".json"}


def _ocr(storage_key: str) -> str:
    """OCR fallback for scanned/image-only PDFs — not wired yet (Task 3.8)."""
    raise NotImplementedError("OCR (Surya) not wired yet — scanned PDF")


def _extract_pdf(storage_key: str) -> str:
    import fitz

    with open_blob(storage_key) as f:
        data = f.read()

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    if not text.strip():
        return _ocr(storage_key)
    return text


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
