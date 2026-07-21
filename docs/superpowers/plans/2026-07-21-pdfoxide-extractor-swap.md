# PDFOxide Extractor + OCR Swap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PyMuPDF text extraction + Surya OCR with PDFOxide (PaddleOCR-v4 ONNX) behind the unchanged `extract_text()` entrypoint, removing Surya's ~10 GB RAM footprint.

**Architecture:** All PDF extraction funnels through one sync function, `app/rag/extract.py::extract_text`. We rewrite only its PDF branch to open via PDFOxide (with a PyMuPDF repair pre-pass for malformed files) and read each page with `extract_text_auto` (auto-routes text-layer vs OCR). Surya's module and dep are deleted. onnxruntime's dylib is wired at app startup; the 21 MB OCR models are provisioned by a script. All three ingress paths (KB upload, inline upload, chat-send) are untouched — they already call `extract_text`.

**Tech Stack:** Python 3.10, FastAPI, `pdf_oxide==0.3.74` (Rust core + PaddleOCR-ONNX), `onnxruntime`, PyMuPDF (`fitz`, retained only for the repair pre-pass), SQLAlchemy, Qdrant, pytest, uv.

## Global Constraints

- Output is **flat plain text** — do NOT adopt `to_markdown`/table extraction this pass.
- `extract_text(storage_key, filename) -> str` stays **synchronous** with the same signature and return contract. No call site changes.
- Scope is the **extractor swap only**. Do NOT touch the ingest/route lifecycle (ingest-decoupling "B" and the reaper "C" are separate specs).
- Keep `python-docx`, plain-text decode, and the image/vision rail unchanged.
- Keep PyMuPDF as a dependency — repair pre-pass only, not the primary extractor.
- Pin `pdf_oxide==0.3.74`.
- Run all commands from `backend/`. Prefix pytest with `env -u VIRTUAL_ENV uv run`.
- Spec: `docs/superpowers/specs/2026-07-21-pdfoxide-extractor-swap-design.md`.

---

## File Structure

- `backend/pyproject.toml` — deps: `+pdf_oxide +onnxruntime`, `-surya-ocr` (Tasks 1, 2).
- `backend/app/config.py` — `+ocr_languages`, `+pdf_oxide_model_dir`; `-ocr_min_chars_per_page` (Tasks 1, 2).
- `backend/app/rag/extract.py` — rewrite the PDF branch to PDFOxide + repair pre-pass; remove `_is_thin`/`_ocr_pdf` (Task 2).
- `backend/app/rag/ocr.py` — **deleted** (Task 2).
- `backend/app/rag/ocr_models.py` — **new**: `ensure_ort_dylib()` (Task 3), `prefetch_ocr_models()` (Task 4).
- `backend/app/main.py` — call `ensure_ort_dylib()` in `lifespan` (Task 3).
- `backend/scripts/setup_ocr_models.py` — **new**: provisioning CLI (Task 4).
- `backend/tests/test_extract.py` — rework OCR tests to PDFOxide (Task 2).
- `backend/tests/test_ingest.py` — retarget the OCR-failure test (Task 2).
- `backend/tests/test_ocr.py` — **deleted** (Task 2).
- `backend/tests/test_ocr_models.py` — **new** (Tasks 3, 4).
- `docs/BACKEND_SPEC.md`, `docs/DEPLOY.md` — amend OCR sections (Task 5).

---

### Task 1: Add PDFOxide/onnxruntime deps + new config fields (non-breaking scaffolding)

Adds the new dependency and config surface **without removing Surya yet**, so the app and suite stay green at this boundary.

**Files:**
- Modify: `backend/pyproject.toml` (dependencies list)
- Modify: `backend/app/config.py:53-59` (OCR config block)
- Test: `backend/tests/test_config_ocr.py` (create)

**Interfaces:**
- Produces: `settings.ocr_languages: list[str]` (default `["english", "latin"]`), `settings.pdf_oxide_model_dir: str | None` (default `None`). `settings.ocr_enabled: bool` unchanged. `import pdf_oxide` and `import onnxruntime` resolve.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config_ocr.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_config_ocr.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'ocr_languages'` (and/or `ModuleNotFoundError: pdf_oxide` if not yet a tracked dep).

- [ ] **Step 3: Add the dependencies**

Run:
```bash
uv add "pdf_oxide==0.3.74" onnxruntime
```
Expected: `pyproject.toml` gains both; `uv.lock` updates; install succeeds.

- [ ] **Step 4: Add the config fields**

In `backend/app/config.py`, replace the OCR block (currently lines ~53-55):

```python
    # --- OCR (§6, §8.1): Surya, in-process, only for thin/absent text layers ---
    ocr_enabled: bool = True
    ocr_min_chars_per_page: int = 100  # below this average, treat the PDF as scanned
```

with:

```python
    # --- OCR (§6, §8.1): PDFOxide / PaddleOCR-v4 ONNX (see 2026-07-21 spec) ---
    ocr_enabled: bool = True  # when False, extract native text only (no OCR)
    # Recognition models to provision (Latin covers the Indonesian corpus).
    ocr_languages: list[str] = ["english", "latin"]
    # Optional override for the PDFOxide model cache (PDF_OXIDE_MODEL_DIR).
    pdf_oxide_model_dir: str | None = None
    # NOTE: ocr_min_chars_per_page is intentionally dropped in Task 2 — PDFOxide
    # routes text-vs-OCR internally. Left here until extract.py stops using it.
    ocr_min_chars_per_page: int = 100
```

(We keep `ocr_min_chars_per_page` for now so `extract.py` still imports; Task 2 removes both it and its usage together.)

- [ ] **Step 5: Run test to verify it passes**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_config_ocr.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/config.py backend/tests/test_config_ocr.py
git commit -m "chore(rag): add pdf_oxide+onnxruntime deps and PDFOxide OCR config fields"
```

---

### Task 2: Swap `extract.py` to PDFOxide and remove Surya (atomic engine swap)

The core change. Rewrites the PDF branch, deletes Surya, and updates all affected tests in one reviewable unit (these are coupled — removing the Surya dep breaks the Surya tests, so they move together).

**Files:**
- Modify: `backend/app/rag/extract.py` (PDF branch)
- Delete: `backend/app/rag/ocr.py`
- Delete: `backend/tests/test_ocr.py`
- Modify: `backend/tests/test_extract.py` (OCR routing tests)
- Modify: `backend/tests/test_ingest.py` (OCR-failure test)
- Modify: `backend/pyproject.toml` (remove `surya-ocr`)
- Modify: `backend/app/config.py` (remove `ocr_min_chars_per_page`)

**Interfaces:**
- Consumes: `settings.ocr_enabled` (Task 1).
- Produces: `extract_text(storage_key: str, filename: str) -> str` (unchanged signature); helpers `_open_pdf_oxide(data: bytes) -> pdf_oxide.PdfDocument` and `_extract_pdf(storage_key: str) -> str`.

- [ ] **Step 1: Write the failing tests (rewrite `test_extract.py` OCR section)**

Open `backend/tests/test_extract.py`. Update the module docstring (drop the Surya/`ocr_images` references) and replace the OCR routing tests with these. Keep the existing text-layer / docx / plain-text / unsupported tests as they are.

```python
def test_extract_text_layer_pdf_uses_native_text(blob_dir):
    """A real text PDF extracts its text via PDFOxide (no OCR needed)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello from a text layer.")
    key = _write(blob_dir, "text.pdf", doc.write())
    doc.close()

    out = extract_text(key, "text.pdf")
    assert "Hello from a text layer." in out


def test_extract_pdf_routes_to_ocr_when_enabled(blob_dir, monkeypatch):
    """ocr_enabled=True → each page read via extract_text_auto (OCR-capable)."""
    import fitz
    import pdf_oxide

    from app.config import settings
    monkeypatch.setattr(settings, "ocr_enabled", True)

    doc = fitz.open()
    doc.new_page()
    key = _write(blob_dir, "scan.pdf", doc.write())
    doc.close()

    monkeypatch.setattr(
        pdf_oxide.PdfDocument, "extract_text_auto",
        lambda self, page: OCR_SENTINEL, raising=True,
    )
    out = extract_text(key, "scan.pdf")
    assert OCR_SENTINEL in out


def test_extract_pdf_skips_ocr_when_disabled(blob_dir, monkeypatch):
    """ocr_enabled=False → native extract_text only; extract_text_auto NOT called."""
    import fitz
    import pdf_oxide

    from app.config import settings
    monkeypatch.setattr(settings, "ocr_enabled", False)

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "native only")
    key = _write(blob_dir, "native.pdf", doc.write())
    doc.close()

    def _boom(self, page):
        raise AssertionError("extract_text_auto must not be called when ocr_enabled=False")

    monkeypatch.setattr(pdf_oxide.PdfDocument, "extract_text_auto", _boom, raising=True)
    out = extract_text(key, "native.pdf")
    assert "native only" in out


def test_open_pdf_oxide_repairs_malformed_pdf(blob_dir, monkeypatch):
    """A PDF PDFOxide can't open is repaired via PyMuPDF, then opened."""
    import fitz
    import pdf_oxide

    from app.rag.extract import _open_pdf_oxide

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "repair me")
    data = doc.write()
    doc.close()

    real_from_bytes = pdf_oxide.PdfDocument.from_bytes
    calls = {"n": 0}

    def flaky_from_bytes(b):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Invalid cross-reference table")
        return real_from_bytes(b)

    monkeypatch.setattr(
        pdf_oxide.PdfDocument, "from_bytes", staticmethod(flaky_from_bytes), raising=True
    )
    pdoc = _open_pdf_oxide(data)
    assert calls["n"] == 2  # first raised, repaired bytes opened on retry
    assert "repair me" in pdoc.extract_text(0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_extract.py -q`
Expected: FAIL — the text-layer PDF still routes through the old PyMuPDF/`_is_thin` path (assertion or `ImportError` on `_open_pdf_oxide`, which doesn't exist yet).

- [ ] **Step 3: Rewrite the PDF branch of `extract.py`**

Replace the top-of-file docstring and the three functions `_is_thin`, `_ocr_pdf`, `_extract_pdf` with:

```python
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
```

Leave `_extract_docx`, `_extract_plain_text`, and `extract_text` (the dispatcher) exactly as they are.

- [ ] **Step 4: Delete the Surya module and its test**

```bash
git rm backend/app/rag/ocr.py backend/tests/test_ocr.py
```

- [ ] **Step 5: Remove the `surya-ocr` dependency and stale config field**

Remove Surya from deps:
```bash
uv remove surya-ocr
```
Then in `backend/pyproject.toml`, update the transformers pin comment (it justified the pin by Surya; the pin stays for FlagEmbedding). Change:
```
    "transformers>=4.56.1,<5",  # surya-ocr 0.17 needs 4.x (see rag extra note)
```
to:
```
    "transformers>=4.56.1,<5",  # pin retained for FlagEmbedding/BGE-M3 compat
```
Also scan `pyproject.toml` for any other `surya` mention (e.g. the line-25/line-34 comments) and delete those comment lines.

In `backend/app/config.py`, delete the now-unused field and its holdover note:
```python
    # NOTE: ocr_min_chars_per_page is intentionally dropped in Task 2 — PDFOxide
    # routes text-vs-OCR internally. Left here until extract.py stops using it.
    ocr_min_chars_per_page: int = 100
```

- [ ] **Step 6: Retarget the ingest OCR-failure test**

In `backend/tests/test_ingest.py`, replace `test_ingest_marks_error_on_ocr_failure` (which monkeypatches the deleted `app.rag.ocr.ocr_images`) with:

```python
def test_ingest_marks_error_on_pdf_extraction_failure(db_session, qdrant: QdrantClient, tmp_path, monkeypatch):
    """PDFOxide extraction failure on a PDF -> status=error (not left at indexing)."""
    import fitz
    import pdf_oxide

    storage_key = "broken.pdf"
    doc = fitz.open()
    doc.new_page()
    (tmp_path / storage_key).write_bytes(doc.write())
    doc.close()

    user = _make_user(db_session)
    file = _make_kb_file(db_session, user.id, storage_key, "broken.pdf")

    def _boom(self, page):
        raise RuntimeError("pdfoxide boom")

    monkeypatch.setattr(pdf_oxide.PdfDocument, "extract_text_auto", _boom, raising=True)

    fake = _FakeEmbedder()
    with pytest.raises(RuntimeError, match="pdfoxide boom"):
        asyncio.run(_collect(ingest(db_session, file.id, client=qdrant, embedder=fake)))

    db_session.refresh(file)
    assert file.status == "error", f"Expected status='error', got '{file.status}'"
    assert file.chunk_count == 0
```

- [ ] **Step 7: Run the affected suites to verify green**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_extract.py tests/test_ingest.py tests/test_config_ocr.py -q`
Expected: PASS (all green; no reference to `app.rag.ocr` remains).

- [ ] **Step 8: Verify no lingering Surya references**

Run: `grep -rniE "surya|ocr_images|app.rag.ocr|ocr_min_chars" backend/app backend/tests`
Expected: no matches.

- [ ] **Step 9: Commit**

```bash
git add -A backend/app/rag/extract.py backend/app/config.py backend/pyproject.toml backend/uv.lock backend/tests/test_extract.py backend/tests/test_ingest.py
git rm --cached backend/app/rag/ocr.py backend/tests/test_ocr.py 2>/dev/null; true
git commit -m "feat(rag): extract PDFs via PDFOxide (PaddleOCR) and remove Surya

Rewrite extract.py's PDF branch to open via PDFOxide (PyMuPDF repair pre-pass
for malformed files) and read each page with extract_text_auto, which OCRs
scanned pages with PaddleOCR-v4 ONNX (~150 MB vs Surya ~10 GB). Delete the
Surya module + dep. extract_text stays sync; all ingress paths unchanged."
```

---

### Task 3: Wire onnxruntime's dylib at startup

PDFOxide's OCR engine dynamically loads onnxruntime; without the dylib on its search path, OCR panics and silently degrades to native (empty) text. Point `ORT_DYLIB_PATH` at the installed onnxruntime library during app startup.

**Files:**
- Create: `backend/app/rag/ocr_models.py`
- Modify: `backend/app/main.py` (`lifespan`)
- Test: `backend/tests/test_ocr_models.py` (create)

**Interfaces:**
- Produces: `app.rag.ocr_models.ensure_ort_dylib() -> str | None` — sets `os.environ["ORT_DYLIB_PATH"]` to the onnxruntime shared library and returns it (or `None` if onnxruntime/dylib is absent). Idempotent.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ocr_models.py`:

```python
"""Tests for OCR runtime/model provisioning helpers."""
from __future__ import annotations

import os


def test_ensure_ort_dylib_sets_env(monkeypatch):
    monkeypatch.delenv("ORT_DYLIB_PATH", raising=False)
    from app.rag.ocr_models import ensure_ort_dylib

    path = ensure_ort_dylib()
    assert path is not None
    assert path.endswith((".dylib", ".so")) or ".so." in path or ".dylib." in path
    assert os.environ["ORT_DYLIB_PATH"] == path


def test_ensure_ort_dylib_is_idempotent(monkeypatch):
    from app.rag.ocr_models import ensure_ort_dylib

    monkeypatch.setenv("ORT_DYLIB_PATH", "/preset/libonnxruntime.dylib")
    assert ensure_ort_dylib() == "/preset/libonnxruntime.dylib"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_ocr_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.ocr_models'`.

- [ ] **Step 3: Create the helper module**

Create `backend/app/rag/ocr_models.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_ocr_models.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Call it from app startup**

In `backend/app/main.py`, inside the `lifespan` function (starts at ~L65), add near the top of startup (before yield), after existing imports:

```python
    # OCR (PDFOxide/PaddleOCR) loads onnxruntime dynamically — point it at the
    # installed dylib so scanned-PDF OCR works; degrades to native text if absent.
    from app.rag.ocr_models import ensure_ort_dylib

    ensure_ort_dylib()
```

- [ ] **Step 6: Verify app still starts (import smoke)**

Run: `env -u VIRTUAL_ENV uv run python -c "import app.main; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 7: Commit**

```bash
git add backend/app/rag/ocr_models.py backend/app/main.py backend/tests/test_ocr_models.py
git commit -m "feat(rag): wire onnxruntime dylib (ORT_DYLIB_PATH) at startup for OCR"
```

---

### Task 4: OCR model provisioning script

The 21 MB PaddleOCR ONNX models must exist before OCR runs. Add a helper + a CLI to download them (air-gap path documented in Task 5).

**Files:**
- Modify: `backend/app/rag/ocr_models.py` (add `prefetch_ocr_models`)
- Create: `backend/scripts/setup_ocr_models.py`
- Test: `backend/tests/test_ocr_models.py` (extend)

**Interfaces:**
- Consumes: `settings.ocr_languages` (Task 1).
- Produces: `app.rag.ocr_models.prefetch_ocr_models(languages: list[str]) -> str` — downloads detector + per-language recognizer into the PDFOxide model cache, returns the cache dir.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_ocr_models.py`:

```python
def test_prefetch_ocr_models_delegates_to_pdfoxide(monkeypatch):
    import pdf_oxide

    called = {}

    def fake_prefetch(languages):
        called["languages"] = languages
        return "/tmp/models"

    monkeypatch.setattr(pdf_oxide.pdf_oxide, "prefetch_models", fake_prefetch)

    from app.rag.ocr_models import prefetch_ocr_models

    out = prefetch_ocr_models(["english", "latin"])
    assert out == "/tmp/models"
    assert called["languages"] == ["english", "latin"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_ocr_models.py::test_prefetch_ocr_models_delegates_to_pdfoxide -q`
Expected: FAIL — `ImportError: cannot import name 'prefetch_ocr_models'`.

- [ ] **Step 3: Add the helper**

Append to `backend/app/rag/ocr_models.py`:

```python
def prefetch_ocr_models(languages: list[str]) -> str:
    """Download PaddleOCR-ONNX detector + per-language recognizers (~21 MB).

    Returns the model cache dir. Wraps PDFOxide's downloader; requires network
    (air-gapped deploys use `pdf_oxide.pdf_oxide.model_manifest()` instead).
    """
    import pdf_oxide

    return pdf_oxide.pdf_oxide.prefetch_models(languages)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_ocr_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Create the provisioning CLI**

Create `backend/scripts/setup_ocr_models.py`:

```python
#!/usr/bin/env python
"""Provision PDFOxide's PaddleOCR-ONNX models (~21 MB). Run once per deploy.

    env -u VIRTUAL_ENV uv run python scripts/setup_ocr_models.py

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
```

- [ ] **Step 6: Verify the script runs (manifest mode — no network)**

Run: `env -u VIRTUAL_ENV uv run python scripts/setup_ocr_models.py --manifest`
Expected: prints a JSON blob containing `"detector"` and `"languages"`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/rag/ocr_models.py backend/scripts/setup_ocr_models.py backend/tests/test_ocr_models.py
git commit -m "feat(rag): OCR model provisioning helper + setup_ocr_models.py script"
```

---

### Task 5: Amend BACKEND_SPEC and DEPLOY docs

Record the engine change in the locked spec (sign-off already given) and document provisioning.

**Files:**
- Modify: `docs/BACKEND_SPEC.md` (§6 and §8.1 OCR references)
- Modify: `docs/DEPLOY.md` (provisioning steps)

- [ ] **Step 1: Locate the OCR references**

Run: `grep -niE "surya|ocr" docs/BACKEND_SPEC.md`
Expected: lists the §6 / §8.1 lines naming Surya.

- [ ] **Step 2: Amend BACKEND_SPEC.md**

At each Surya reference in §6 and §8.1, replace the Surya wording with (adapt to surrounding prose):

```
OCR is performed by PDFOxide (PaddleOCR-v4 ONNX, on onnxruntime/CPU), in-process,
routed automatically per page (native text layer vs scanned). This supersedes the
original Surya OCR design (dropped for its ~10 GB RAM footprint; see
docs/superpowers/specs/2026-07-21-pdfoxide-extractor-swap-design.md). PyMuPDF is
retained solely as a repair pre-pass for malformed PDFs. Runtime requires the
onnxruntime shared library (ORT_DYLIB_PATH, set at startup) and the ~21 MB OCR
models (see scripts/setup_ocr_models.py).
```

- [ ] **Step 3: Add provisioning to DEPLOY.md**

Add an "OCR models" subsection to `docs/DEPLOY.md`:

```
### OCR models (PDFOxide / PaddleOCR)

Scanned-PDF OCR needs onnxruntime (installed as a dependency) and ~21 MB of
ONNX models. Provision once per deploy:

    cd backend && env -u VIRTUAL_ENV uv run python scripts/setup_ocr_models.py

Air-gapped hosts (no outbound internet): run `... setup_ocr_models.py --manifest`
on a connected machine, fetch the listed files, and drop them into the directory
named by PDF_OXIDE_MODEL_DIR on the target. The app sets ORT_DYLIB_PATH itself at
startup; if onnxruntime is missing, OCR degrades to native text (no crash).
```

- [ ] **Step 4: Commit**

```bash
git add docs/BACKEND_SPEC.md docs/DEPLOY.md
git commit -m "docs: amend OCR sections for PDFOxide swap; document model provisioning"
```

---

### Task 6: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `env -u VIRTUAL_ENV uv run pytest -q`
Expected: PASS — parity with the pre-swap baseline (333 passed, 1 Docker-skip), plus the new config/extract/ocr_models tests; **0 failures**. (The suite takes ~15 min; the sandbox Docker integration test still skips.)

- [ ] **Step 2: Real-file smoke check (optional, needs models)**

Run: `env -u VIRTUAL_ENV uv run python scripts/setup_ocr_models.py && env -u VIRTUAL_ENV uv run python scripts/check_pdf_pipeline.py`
Expected: text-layer PDFs extract text; the 195-page scanned PDF OCRs at ~150 MB RSS. (This is the probe from branch `probe/pdfoxide-pipeline`; cherry-pick it if not present.)

- [ ] **Step 3: Confirm branch state**

Run: `git log --oneline main..HEAD`
Expected: the six commits above, on `feat/pdfoxide-extractor-swap`, ready to merge per the branch-per-stage convention.

---

## Self-Review

**Spec coverage:**
- §2 decision (PDFOxide replaces PyMuPDF+Surya) → Task 2. ✅
- §4.1 extract.py rewrite (`extract_text_auto` / native gate) → Task 2, Step 3. ✅
- §4.2 repair pre-pass → Task 2 (`_open_pdf_oxide`) + test. ✅
- §4.3 delete ocr.py/test_ocr.py → Task 2, Step 4. ✅
- §4.4 deps (−surya +pdf_oxide +onnxruntime; transformers pin) → Tasks 1 & 2. ✅
- §4.5 config (keep `ocr_enabled`, drop `ocr_min_chars_per_page`, add `ocr_languages`/`pdf_oxide_model_dir`) → Tasks 1 & 2. ✅
- §4.6 ORT_DYLIB_PATH at startup → Task 3. ✅
- §4.7 model provisioning (script + manifest air-gap) → Task 4. ✅
- §5.1 plain-text output → Task 2 uses `extract_text`/`extract_text_auto` (no markdown). ✅
- §6 testing (delete test_ocr, rework test_extract, retarget ingest OCR test) → Task 2. ✅
- §8 BACKEND_SPEC amendment → Task 5. ✅
- §5.2 migration (no re-ingest; reindex opportunity) → documented in spec; no code task needed (existing `/reindex` route unchanged). ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✅

**Type consistency:** `extract_text(storage_key, filename) -> str`, `_open_pdf_oxide(data) -> PdfDocument`, `ensure_ort_dylib() -> str | None`, `prefetch_ocr_models(languages) -> str` used consistently across tasks. `settings.ocr_languages` produced in Task 1, consumed in Task 4. `settings.ocr_enabled` consumed in Task 2. ✅
