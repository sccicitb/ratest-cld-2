# PDFOxide extractor + OCR swap — design

- **Date:** 2026-07-21
- **Status:** Draft (awaiting review)
- **Scope decision:** Extractor swap only. Ingest-decoupling (B) and stale-row reaper (C) are a **separate** reliability spec.
- **Related:** `docs/BACKEND_SPEC.md` §6/§8.1 (this spec amends them); probe `backend/scripts/check_pdf_pipeline.py` (branch `probe/pdfoxide-pipeline`); memory `pdfoxide-replaces-surya`.

## 1. Problem

Surya OCR fires in-process on thin/scanned PDFs and **bloats RAM to ~10 GB** when it loads its torch model stack. This is the driver for replacing it. Separately, our PDF text extraction (PyMuPDF `get_text`) is flat and PyMuPDF is AGPL-licensed.

## 2. Decision

Replace **both** PyMuPDF (text-layer extraction) and Surya (OCR) with a single dependency, **PDFOxide** (`pip install pdf_oxide`, Rust core, MIT/Apache), whose built-in OCR is **PaddleOCR-v4 ONNX on onnxruntime (CPU)**.

Empirically validated on the three real blobs in `backend/data/blobs` (see the committed probe):

| Axis | Surya (today) | PDFOxide / PaddleOCR-ONNX |
|---|---|---|
| Peak RAM (scanned OCR) | ~10,000 MB | **~150 MB** (~66 MB delta) |
| Speed | slow | ~1.0s first page, **~0.3s/page** steady |
| Models on disk | multi-GB torch | **21 MB ONNX** (air-gappable) |
| Provider dependency | none | none (fully local) |
| License | Surya Open-RAIL-M / PyMuPDF AGPL | MIT/Apache |

## 3. Goals / Non-goals

**Goals**
- Remove Surya and its ~10 GB RAM footprint; delete `app/rag/ocr.py`.
- Route all PDF extraction (text-layer + scanned OCR) through PDFOxide, behind the unchanged `extract_text(storage_key, filename) -> str` entrypoint.
- Keep the three ingress paths (KB upload, inline upload, chat-send re-extract) **untouched**.
- Provision the 21 MB OCR models and the onnxruntime dylib for the prod Linux VM.

**Non-goals (explicitly out of this spec)**
- **B — decoupling ingest from the HTTP connection** (background task). Separate reliability spec. The blocking-event-loop behavior during OCR is unchanged from today (Surya was also sync); not a regression, not addressed here.
- **C — reaper for crash-stranded `indexing` rows.** Separate reliability spec.
- **Figure captioning via VLM** ("images as first-class documents"). Documented fast-follow, not built here.
- **Rich markdown / table extraction.** We deliberately keep **flat plain-text** output (see §5.1). The "flat extraction" gap stays open by choice — lowest risk, no chunker-compat work.
- **docx / plain-text / image handling.** Unchanged (`python-docx`, UTF-8 decode, vision rail all stay).

## 4. Architecture

All extraction funnels through one function — `extract_text()` in `app/rag/extract.py`. The swap is centralized there; callers are unaffected.

```
KB upload      ─ ingest.py:47          ─┐
inline upload  ─ attachments.py:144    ─┼─▶ extract_text(storage_key, filename) ─▶ str
chat-send      ─ chat/routes.py:98     ─┘        (SYNC, same signature, same return contract)
                                                        │
                              ┌─────────────────────────┴───────────────────────────┐
                              ▼                                                       ▼
                    .pdf → PDFOxide                                    .docx → python-docx (unchanged)
                      open (+ repair pre-pass)                         .md/.txt/.csv/.json → decode (unchanged)
                      per page: extract_text_auto  (text-layer → text; scanned → PaddleOCR)
```

`extract_text` **stays synchronous** with the same `-> str` return, so no call site changes and no async refactor. (PaddleOCR is a sync Rust call, like Surya was.)

### 4.1 `app/rag/extract.py` (rewrite the PDF branch)

- `_extract_pdf(storage_key)`:
  - Read blob bytes; open via `_open_pdf_oxide(data)` (repair pre-pass, §4.2).
  - Per page, call the PDFOxide extractor and join with `"\n"`:
    - `settings.ocr_enabled == True` → `doc.extract_text_auto(page)` — auto-routes text-vs-OCR with graceful native fallback.
    - `settings.ocr_enabled == False` → PDFOxide **native-text** method (no OCR), preserving today's "OCR disabled → surface whatever text exists" behavior.
  - (Exact native-text method name — `extract_text` vs `to_plain_text` — pinned during the plan against `pdf_oxide==0.3.74`.)
- `_ocr_pdf` and `_is_thin` are **removed** — PDFOxide does the text-vs-OCR routing internally, so `ocr_min_chars_per_page` no longer has a job.
- `_extract_docx`, `_extract_plain_text`, `.doc` error, and `SUPPORTED_KB_TYPES` are **unchanged**.

### 4.2 Malformed-PDF repair pre-pass

PDFOxide is stricter than PyMuPDF about broken PDFs (one real blob failed with "Invalid cross-reference table"). `_open_pdf_oxide(data)`:

```
try:    PdfDocument.from_bytes(data)
except: clean = fitz.open(stream=data).tobytes(garbage=4, clean=True, deflate=True)
        PdfDocument.from_bytes(clean)
```

This is why **PyMuPDF stays a dependency** — as the repair fallback only, not the primary extractor.

### 4.3 Delete Surya

- Delete `app/rag/ocr.py` (whole module).
- Delete `backend/tests/test_ocr.py` (the real-Surya proof).

### 4.4 Dependencies (`backend/pyproject.toml`)

- **Remove:** `surya-ocr>=0.6`.
- **Re-evaluate:** the `transformers>=4.56.1,<5` pin exists *for* Surya. FlagEmbedding/BGE-M3 also uses transformers — verify it still resolves; keep the pin if needed but drop the Surya justification. (Verified in the plan, not assumed.)
- **Keep:** `pymupdf` (repair pre-pass), `python-docx`.
- **Add:** `pdf_oxide>=0.3` (pin `==0.3.74` given maturity), `onnxruntime>=1.17`.

### 4.5 Config (`app/config.py`)

- **Keep** `ocr_enabled: bool = True` (gate).
- **Remove** `ocr_min_chars_per_page` (PDFOxide routes internally).
- **Add** `ocr_languages: list[str] = ["english", "latin"]` (models to provision; `latin` covers the Indonesian corpus — Latin script).
- **Add** `pdf_oxide_model_dir: str | None = None` (optional `PDF_OXIDE_MODEL_DIR` override; default is the platform cache).

### 4.6 Startup — onnxruntime dylib (`app/main.py` lifespan)

PDFOxide's OCR engine dynamically loads onnxruntime; if the dylib isn't found the OCR call panics. In the `lifespan` startup (L65), set `ORT_DYLIB_PATH` to the installed onnxruntime shared library (locate via the `onnxruntime` package; `.so` on the Linux VM). Idempotent; skip if already set. Log a clear warning if onnxruntime is missing (OCR will fall back to native text rather than crash).

### 4.7 OCR model provisioning (deploy)

The 21 MB PaddleOCR ONNX models (detector + per-language recognizer) must exist before OCR runs. Add `backend/scripts/setup_ocr_models.py` that calls `pdf_oxide.pdf_oxide.prefetch_models(settings.ocr_languages)` into the model cache. Run once per deploy.

- **Air-gapped prod** (no public IP, per deployment topology): `pdf_oxide.pdf_oxide.model_manifest()` emits the exact file list + URLs; fetch on a connected host and drop into `PDF_OXIDE_MODEL_DIR`. Documented in `docs/DEPLOY.md`.

## 5. Behavior details

### 5.1 Output format — plain text (decided)

Text-layer PDFs produce **flat plain text**, matching today's contract. We do **not** adopt `to_markdown`/table extraction in this spec (lower risk, no chunker changes). Revisit as a future enhancement if RAG quality on tabular docs warrants it.

### 5.2 Existing KB files / migration

- No re-ingest required: existing chunks in Qdrant are plain text and remain valid.
- New uploads use PDFOxide immediately.
- **Opportunity (optional):** documents previously stuck/`error` on scanned PDFs (Surya RAM failures) can now be **reindexed** via the existing `/{file_id}/reindex` route and will succeed with PaddleOCR. Call out in release notes; no automatic migration.

## 6. Testing

- **Delete** `tests/test_ocr.py`.
- **`tests/test_extract.py`:** drop the `app.rag.ocr.ocr_images` monkeypatch. Add:
  - text-layer PDF → returns its text (real PDFOxide, fast).
  - malformed-xref PDF → opens via repair pre-pass and returns text.
  - scanned PDF → OCR path. Prefer monkeypatching the PDFOxide extractor for a fast unit test; a real-OCR proof (needs models) is an **integration test** marked to skip when models/onnxruntime are absent (mirrors the sandbox Docker skip pattern).
  - `ocr_enabled=False` → native text only, no OCR.
- **`tests/test_ingest.py::test_ingest_marks_error_on_ocr_failure`:** currently monkeypatches `app.rag.ocr.ocr_images` (gone). Retarget to force the PDFOxide extraction to raise, asserting `status="error"`. The cancellation-orphan test (already merged) is unaffected.
- Full suite must stay green (currently 333 passed, 1 Docker-skip).

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| PDFOxide maturity (v0.3, single maintainer) | Pin `==0.3.74`; keep PyMuPDF for repair; validated on real docs; escape hatch = revert one file. |
| OCR quality regression vs Surya on some scans | PaddleOCR is recall-adequate (keywords survive); tunable via `OcrConfig` thresholds / language / dpi in a follow-up. |
| onnxruntime dylib missing on prod VM | Startup sets `ORT_DYLIB_PATH` + clear warning; graceful native fallback, no crash. |
| Air-gapped model provisioning | `model_manifest()` + documented offline placement. |
| Event-loop blocking during long OCR | Unchanged from Surya (not a regression); addressed by spec **B**, out of scope here. |

## 8. BACKEND_SPEC.md amendment (deliverable of this spec)

`docs/BACKEND_SPEC.md` §6 and §8.1 currently mandate Surya OCR. Amend both to specify **PDFOxide / PaddleOCR-v4 ONNX** as the OCR + PDF-extraction engine, note the onnxruntime runtime dependency and model provisioning, and record that PyMuPDF is retained solely as the malformed-PDF repair fallback. This amendment is part of the implementation, gated on the sign-off already given for dropping Surya.

## 9. Follow-ups (tracked, not built here)

- **B** — decouple ingest from the HTTP connection (background task) so a client disconnect doesn't interrupt ingest at all; complements the already-merged cancellation-orphan fix.
- **C** — startup reaper sweeping any `indexing` rows stranded by a hard crash.
- **Figure captioning** — extract embedded figures (`extract_image_bytes`) → VLM caption → embed ("images as first-class documents"). Qwen3.6 vision validated; DeepSeek API rejects images.
- **Rich extraction** — revisit `to_markdown` + `extract_tables` if tabular-doc RAG quality demands it.
