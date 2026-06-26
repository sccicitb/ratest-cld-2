"""Surya OCR — in-process recognition for scanned/image-heavy PDFs (§8.1).

Surya's predictors are heavy (model load ~10-20s), so they're built once per
process via `get_surya()` and cached. `ocr_images()` is the public entry
point; it delegates to `_run_ocr()` so tests can monkeypatch just the model
call without ever importing/loading Surya.
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from PIL.Image import Image


class SuryaPredictors(NamedTuple):
    recognition: object
    detection: object


@lru_cache(maxsize=1)
def get_surya() -> SuryaPredictors:
    """Process-singleton: loads Surya's recognition + detection models once."""
    from surya.detection import DetectionPredictor
    from surya.foundation import FoundationPredictor
    from surya.recognition import RecognitionPredictor

    rec = RecognitionPredictor(FoundationPredictor())
    det = DetectionPredictor()
    return SuryaPredictors(recognition=rec, detection=det)


def _run_ocr(images: list[Image]) -> list[str]:
    """Call Surya's recognition predictor. Isolated so tests can monkeypatch it."""
    predictors = get_surya()
    results = predictors.recognition(images, det_predictor=predictors.detection)
    return [" ".join(line.text for line in result.text_lines) for result in results]


def ocr_images(images: list[Image]) -> list[str]:
    """OCR a list of page images, returning one text string per image."""
    if not images:
        return []
    return _run_ocr(images)
