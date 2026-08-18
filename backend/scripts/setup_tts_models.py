#!/usr/bin/env python
"""Provision Supertonic 3 assets for an air-gapped host (voice §1b).

Mirrors setup_ocr_models.py. On a connected machine:

    env -u VIRTUAL_ENV uv run --with huggingface-hub \
        python scripts/setup_tts_models.py --dest ../tts_models

Then copy that directory to the target and set TTS_MODEL_DIR to it.

`--manifest` prints the file list without downloading, for hosts that must
fetch through something other than this script.
"""
from __future__ import annotations

import argparse
from pathlib import Path

REPO = "Supertone/supertonic-3"
FILES = [
    "config.json",
    "onnx/text_encoder.onnx",
    "onnx/duration_predictor.onnx",
    "onnx/vector_estimator.onnx",
    "onnx/vocoder.onnx",
    "onnx/tts.json",
    "onnx/unicode_indexer.json",
] + [f"voice_styles/{v}.json" for v in
     ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=Path("../tts_models"))
    ap.add_argument("--manifest", action="store_true",
                    help="print the URLs instead of downloading")
    args = ap.parse_args()

    if args.manifest:
        for f in FILES:
            print(f"https://huggingface.co/{REPO}/resolve/main/{f}")
        return

    from huggingface_hub import hf_hub_download

    args.dest.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        # local_dir (not cache_dir) so the result is a plain directory tree the
        # runtime can be pointed at directly -- a HF cache layout with its
        # blobs/ and snapshots/ indirection is not what TTS(path) expects.
        path = hf_hub_download(repo_id=REPO, filename=f, local_dir=str(args.dest))
        print(f"ok  {f}  -> {path}")
    print(f"\nSet TTS_MODEL_DIR={args.dest.resolve()}")


if __name__ == "__main__":
    main()
