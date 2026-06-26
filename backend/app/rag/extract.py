"""Document text extraction (§8.1): PyMuPDF + python-docx + plain text.

Routing is by filename extension. PDFs go through PyMuPDF first; we measure
the text layer's density and route to Surya OCR (`app.rag.ocr`) only when
it's thin/absent — a real text PDF never pays for OCR (§6). `.doc` (legacy
binary Word) needs a LibreOffice conversion step that isn't wired here, so
it raises a clear, actionable error.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.storage import open_blob

SUPPORTED_KB_TYPES = {".pdf", ".md", ".txt", ".docx", ".doc", ".csv", ".json"}

_TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".json"}


def _is_thin(text: str, page_count: int) -> bool:
    """A page is "thin" if its text layer averages below the configured floor."""
    if page_count == 0:
        return True
    return (len(text) / page_count) < settings.ocr_min_chars_per_page


def _ocr_pdf(doc) -> str:
    """Render every page to an image and OCR it via Surya."""
    from PIL import Image

    from app.rag.ocr import ocr_images

    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        images.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))

    page_texts = ocr_images(images)
    return "\n".join(page_texts)


def _extract_pdf(storage_key: str) -> str:
    import fitz

    with open_blob(storage_key) as f:
        data = f.read()

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        text = "\n".join(page.get_text() for page in doc)

        if _is_thin(text, doc.page_count) and settings.ocr_enabled:
            return _ocr_pdf(doc)
        # OCR disabled (or text was dense enough): surface whatever PyMuPDF
        # found rather than raising — a thin text layer is still useful for
        # search, and a hard failure here would block ingestion entirely.
        return text
    finally:
        doc.close()


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
