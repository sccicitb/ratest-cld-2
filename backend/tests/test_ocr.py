"""Real Surya OCR proof (Task 3.8): one heavy, module-scoped integration test.

Model load takes ~10-20s, so the predictors are built once via `get_surya()`
(a process singleton — see `app.rag.ocr`) and reused across this module.
This is the single real-OCR proof; routing logic is covered with a fake OCR
in `tests/test_extract.py`.
"""
from __future__ import annotations

import pytest

from app.rag.ocr import get_surya, ocr_images


@pytest.fixture(scope="module")
def _warm_surya():
    """Force the model load once for the module, ahead of the actual test."""
    return get_surya()


def test_ocr_images_recognizes_real_text(_warm_surya):
    import fitz
    from PIL import Image

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Surya OCR integration test", fontsize=24)
    pix = page.get_pixmap(dpi=150)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()

    texts = ocr_images([image])

    assert len(texts) == 1
    recognized = texts[0].lower()
    assert "surya" in recognized
    assert "ocr" in recognized
