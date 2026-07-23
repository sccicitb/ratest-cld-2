"""OCR config surface for the PDFOxide swap."""
from __future__ import annotations


def test_settings_expose_pdfoxide_ocr_fields():
    from app.config import settings

    assert settings.ocr_languages == ["english", "latin"]
    assert settings.pdf_oxide_model_dir is None
    assert settings.ocr_enabled is True


def test_pdfoxide_and_onnxruntime_import():
    import onnxruntime  # noqa: F401
    import pdf_oxide  # noqa: F401
