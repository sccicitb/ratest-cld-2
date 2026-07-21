"""OCR runtime + model provisioning for PDFOxide (PaddleOCR-v4 ONNX).

PDFOxide's OCR engine loads onnxruntime dynamically at runtime. If the shared
library isn't found, OCR panics inside Rust and falls back to native text. We
point ORT_DYLIB_PATH at the onnxruntime shipped with the `onnxruntime` wheel.
"""
from __future__ import annotations

import glob
import os
import sys


def ensure_ort_dylib() -> str | None:
    """Set ORT_DYLIB_PATH to the installed onnxruntime shared library.

    Idempotent: honors an existing ORT_DYLIB_PATH. Returns the path, or None if
    onnxruntime (or its dylib) can't be found — in which case OCR degrades to
    native text rather than crashing.
    """
    existing = os.environ.get("ORT_DYLIB_PATH")
    if existing:
        return existing
    try:
        import onnxruntime
    except ImportError:
        return None
    root = os.path.dirname(onnxruntime.__file__)
    ext = "dylib" if sys.platform == "darwin" else "so"
    hits = glob.glob(f"{root}/**/*onnxruntime*.{ext}*", recursive=True)
    if not hits:
        return None
    os.environ["ORT_DYLIB_PATH"] = hits[0]
    return hits[0]
