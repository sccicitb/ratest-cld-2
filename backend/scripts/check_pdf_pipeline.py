#!/usr/bin/env python
"""Smoke-test the PDFOxide document pipeline (text-layer + OCR) on real PDFs.

This reproduces the exploration that led us to consider replacing PyMuPDF
(text extraction) AND Surya (OCR) with a single dependency: PDFOxide, whose
built-in OCR is PaddleOCR-v4 ONNX running on onnxruntime (CPU). The headline
finding it verifies: scanned-PDF OCR at ~150 MB peak RAM instead of Surya's
~10 GB, deterministic and fully local (no vision model / provider needed).

It runs, on each PDF you point it at:
  1. PROFILE   — pages, text density, per-page scanned-vs-text classification.
  2. TEXT      — PDFOxide to_markdown + extract_tables on a text-layer page
                 (richer than PyMuPDF's flat get_text).
  3. OCR       — PDFOxide extract_text_auto on scanned pages, timing each and
                 reporting PEAK RSS so you can compare against Surya on the box.
  4. VLM       — (optional, --vlm) extract an embedded figure and caption it via
                 an OpenAI-compatible vision model, to gauge the *optional*
                 figure-captioning fast-follow. Provider comes from env (same
                 vars as check_provider_vision.py). DeepSeek's API rejects
                 images; llama-server/Qwen-VL works.

Three real-world snags it handles automatically (each is a deploy caveat):
  - Malformed PDFs (broken xref) that PyMuPDF opens but PDFOxide rejects: a
    PyMuPDF repair pre-pass (tobytes(garbage=4, clean=True)) is applied on
    failure.
  - OCR models absent: prefetched (~21 MB) into the PDFOxide model cache.
  - onnxruntime dylib not found by PDFOxide's OCR engine: ORT_DYLIB_PATH is
    pointed at the pip-installed onnxruntime automatically.

Usage:
    # default: every *.pdf under data/blobs, text + OCR, no VLM
    env -u VIRTUAL_ENV uv run python scripts/check_pdf_pipeline.py

    # specific files, OCR the first 3 scanned pages of each
    ... check_pdf_pipeline.py data/blobs/foo.pdf --ocr-pages 3

    # also exercise the VLM figure-caption path against the active provider
    MODEL_BASE_URL=https://llama.sccic.org/v1 \
    MODEL_NAME='unsloth/Qwen3.6-27B-MTP-GGUF:UD-Q4_K_XL' \
    MODEL_API_KEY=not-needed \
    ... check_pdf_pipeline.py data/blobs/charts.pdf --vlm

Requires: pdf_oxide, onnxruntime, PyMuPDF (fitz), Pillow, openai.
"""
from __future__ import annotations

import argparse
import base64
import glob
import io
import os
import resource
import sys
import time


# ---------------------------------------------------------------------------
# Environment prep (the three deploy caveats, handled up front)
# ---------------------------------------------------------------------------


def peak_rss_mb() -> float:
    """Peak resident set size, MB. ru_maxrss is bytes on macOS, KB on Linux."""
    kb_or_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb_or_bytes / (1024 * 1024) if sys.platform == "darwin" else kb_or_bytes / 1024


def ensure_ort_dylib() -> str | None:
    """Point ORT_DYLIB_PATH at the pip-installed onnxruntime shared library.

    PDFOxide's OCR engine dynamically loads onnxruntime; if the dylib isn't on
    the default search path the OCR call panics. We locate the one shipped with
    the `onnxruntime` wheel and export it (unless already set).
    """
    if os.environ.get("ORT_DYLIB_PATH"):
        return os.environ["ORT_DYLIB_PATH"]
    try:
        import onnxruntime
    except ImportError:
        print("  ! onnxruntime not installed — OCR will fall back to native text.")
        print("    fix: uv pip install onnxruntime")
        return None
    root = os.path.dirname(onnxruntime.__file__)
    ext = "dylib" if sys.platform == "darwin" else "so"
    hits = glob.glob(f"{root}/**/*onnxruntime*.{ext}*", recursive=True)
    if not hits:
        print(f"  ! no libonnxruntime.{ext} found under {root}")
        return None
    os.environ["ORT_DYLIB_PATH"] = hits[0]
    return hits[0]


def ensure_ocr_models(languages: list[str]) -> None:
    """Download PaddleOCR-ONNX models into the PDFOxide cache if missing (~21 MB)."""
    import pdf_oxide as px

    cache = os.environ.get("PDF_OXIDE_MODEL_DIR") or os.path.expanduser(
        "~/.cache/pdf_oxide/models"
    )
    if os.path.isdir(cache) and any(f.endswith(".onnx") for f in os.listdir(cache)):
        return
    if not px.pdf_oxide.prefetch_available():
        print("  ! this pdf_oxide wheel was built without the OCR download feature.")
        return
    print(f"  · prefetching OCR models {languages} …", end="", flush=True)
    t = time.time()
    px.pdf_oxide.prefetch_models(languages)
    print(f" done ({time.time() - t:.1f}s) → {cache}")


# ---------------------------------------------------------------------------
# Opening (with the malformed-PDF repair pre-pass)
# ---------------------------------------------------------------------------


def open_pdf(path: str):
    """Open a PDF with PDFOxide, repairing via PyMuPDF on a broken xref.

    Returns (PdfDocument, repaired: bool). PDFOxide is stricter than PyMuPDF
    about malformed PDFs; when it refuses, we round-trip the bytes through
    PyMuPDF's repair (garbage collect + clean) and retry.
    """
    import pdf_oxide as px

    data = open(path, "rb").read()
    try:
        return px.PdfDocument.from_bytes(data), False
    except Exception:
        import fitz

        clean = fitz.open(stream=data, filetype="pdf").tobytes(
            garbage=4, clean=True, deflate=True
        )
        return px.PdfDocument.from_bytes(clean), True


# ---------------------------------------------------------------------------
# The three tests
# ---------------------------------------------------------------------------


def profile(path: str) -> dict:
    """PyMuPDF profile: pages, total chars, avg chars/page, embedded images."""
    import fitz

    doc = fitz.open(path)
    text = "".join(pg.get_text() for pg in doc)
    n_img = sum(len(pg.get_images()) for pg in doc)
    avg = len(text) / max(doc.page_count, 1)
    info = {
        "pages": doc.page_count,
        "chars": len(text),
        "avg_per_page": avg,
        "images": n_img,
        "scanned": avg < 100,
    }
    doc.close()
    return info


def test_text_layer(doc, page: int) -> None:
    """PDFOxide structured extraction on a text-layer page."""
    print(f"  TEXT (page {page}):")
    if hasattr(doc, "to_markdown"):
        md = doc.to_markdown(page)
        print(f"    to_markdown  → {len(md)} chars; head:\n"
              + "\n".join("      " + ln for ln in md[:300].splitlines()))
    try:
        tables = doc.extract_tables(page)
        print(f"    extract_tables → {len(tables)} table(s)"
              + (f"; first={tables[0].get('row_count')}x{tables[0].get('col_count')}"
                 if tables else ""))
    except Exception as e:
        print(f"    extract_tables → {type(e).__name__}: {str(e)[:80]}")


def test_ocr(doc, pages: int) -> None:
    """PDFOxide auto OCR on the first `pages` pages; report time + peak RSS."""
    print(f"  OCR (first {pages} page(s), auto-routed):")
    print(f"    RSS before OCR: {peak_rss_mb():.0f} MB")
    for pg in range(min(pages, doc.page_count())):
        t = time.time()
        try:
            txt = doc.extract_text_auto(pg)
            head = " ".join(txt.split())[:90]
            print(f"    page {pg}: {time.time() - t:4.1f}s  "
                  f"peakRSS={peak_rss_mb():4.0f} MB  chars={len(txt):4d}  {head!r}")
        except Exception as e:
            print(f"    page {pg}: FAIL {type(e).__name__}: {str(e)[:100]}")


def _first_figure_jpeg(doc, page: int, max_px: int = 1200) -> bytes | None:
    """Extract the first embedded image on `page`, downscaled to a JPEG.

    Raw extracted images can be huge (tens of MB, thousands of px) — too big
    for a VLM. We decode, thumbnail, and re-encode as JPEG.
    """
    from PIL import Image

    try:
        images = doc.extract_image_bytes(page)
    except Exception:
        return None
    if not images:
        return None
    raw = bytes(images[0]["data"]) if isinstance(images[0], dict) else bytes(images[0])
    img = Image.open(io.BytesIO(raw))
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_vlm(doc, page: int) -> None:
    """Caption an embedded figure via an OpenAI-compatible vision model.

    Provider from env: MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME. Sends the
    figure as a downscaled JPEG. Gives the model room (max_tokens=800) and reads
    BOTH content and reasoning_content — thinking models (e.g. Qwen3.6-MTP) spend
    the budget on reasoning and return empty content when max_tokens is small.
    """
    base_url = os.environ.get("MODEL_BASE_URL")
    api_key = os.environ.get("MODEL_API_KEY")
    model = os.environ.get("MODEL_NAME")
    print("  VLM (figure caption):")
    if not (base_url and api_key and model):
        print("    skipped — set MODEL_BASE_URL, MODEL_API_KEY, MODEL_NAME.")
        return
    jpeg = _first_figure_jpeg(doc, page)
    if not jpeg:
        print(f"    no extractable image on page {page}.")
        return

    from openai import APIStatusError, OpenAI

    b64 = base64.b64encode(jpeg).decode()
    client = OpenAI(base_url=base_url, api_key=api_key)
    print(f"    model={model}  fig={len(jpeg) // 1024} KB")
    t = time.time()
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Describe this image in one concise "
                 "sentence for search indexing."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            temperature=0,
            max_tokens=800,
        )
    except APIStatusError as e:
        print(f"    HTTP {e.status_code}: provider rejected the image "
              "(no vision via this API).")
        return
    except Exception as e:  # noqa: BLE001
        print(f"    ERROR {type(e).__name__}: {str(e)[:120]}")
        return
    msg = r.choices[0].message
    content = (msg.content or "").strip()
    reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
    print(f"    {time.time() - t:.1f}s  finish={r.choices[0].finish_reason}")
    if content:
        print(f"    CAPTION: {content}")
    elif reasoning:
        print("    content empty but reasoning present — thinking model ate the "
              "token budget; raise max_tokens / handle reasoning_content.")
    else:
        print("    empty reply (image too large? try smaller --figure size).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*",
                    help="PDF paths (default: data/blobs/*.pdf)")
    ap.add_argument("--ocr-pages", type=int, default=2,
                    help="how many leading pages to OCR on scanned PDFs (default 2)")
    ap.add_argument("--langs", default="english,latin",
                    help="OCR languages to prefetch (default english,latin)")
    ap.add_argument("--no-ocr", action="store_true", help="skip the OCR test")
    ap.add_argument("--vlm", action="store_true",
                    help="also run the VLM figure-caption test (uses MODEL_* env)")
    args = ap.parse_args()

    pdfs = args.pdfs or sorted(glob.glob("data/blobs/*.pdf"))
    if not pdfs:
        print("No PDFs given and none found under data/blobs/.")
        return 2

    print("=== environment ===")
    print(f"  ORT_DYLIB_PATH: {ensure_ort_dylib()}")
    if not args.no_ocr:
        ensure_ocr_models(args.langs.split(","))
    print(f"  baseline RSS: {peak_rss_mb():.0f} MB")

    for path in pdfs:
        print(f"\n=== {os.path.basename(path)} ===")
        info = profile(path)
        kind = "SCANNED" if info["scanned"] else "text-layer"
        print(f"  PROFILE: {info['pages']}pp  {info['chars']} chars  "
              f"avg={info['avg_per_page']:.0f}/pg  imgs={info['images']}  [{kind}]")

        try:
            doc, repaired = open_pdf(path)
        except Exception as e:
            print(f"  open FAILED: {type(e).__name__}: {str(e)[:120]}")
            continue
        if repaired:
            print("  (opened via PyMuPDF repair pre-pass — malformed xref)")

        if not info["scanned"]:
            test_text_layer(doc, 0)
        if info["scanned"] and not args.no_ocr:
            test_ocr(doc, args.ocr_pages)
        if args.vlm:
            test_vlm(doc, 0)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
