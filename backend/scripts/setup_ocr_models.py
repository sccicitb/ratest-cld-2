#!/usr/bin/env python
"""Provision PDFOxide's PaddleOCR-ONNX models (~21 MB). Run once per deploy.

    uv run python scripts/setup_ocr_models.py

Languages come from settings.ocr_languages. For air-gapped hosts, print the
manifest instead and place files manually into PDF_OXIDE_MODEL_DIR:

    ... setup_ocr_models.py --manifest
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", action="store_true",
                    help="print the air-gap model manifest (JSON) and exit")
    args = ap.parse_args()

    import pdf_oxide

    if args.manifest:
        print(pdf_oxide.pdf_oxide.model_manifest())
        return 0

    from app.config import settings
    from app.rag.ocr_models import prefetch_ocr_models

    cache = prefetch_ocr_models(settings.ocr_languages)
    print(f"OCR models ready for {settings.ocr_languages} in {cache}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
