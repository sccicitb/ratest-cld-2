#!/usr/bin/env python
"""Prefetch the faster-whisper model so prod never needs HuggingFace at runtime.

Prod is air-gapped. faster-whisper downloads CTranslate2 weights on first use,
which on that host means a hang and then a failed transcription. Run this once
per deploy -- or, with no outbound internet, run --manifest on a connected
machine, copy the listed files, and set STT_MODEL_DIR on the target.

Usage:
    uv run python scripts/setup_stt_model.py
    uv run python scripts/setup_stt_model.py --model large-v3 --manifest
"""
from __future__ import annotations

import argparse
import os
import sys

REPO_TEMPLATE = "Systran/faster-whisper-{model}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("STT_MODEL", "large-v3-turbo"))
    ap.add_argument("--dir", default=os.environ.get("STT_MODEL_DIR") or None)
    ap.add_argument("--manifest", action="store_true",
                    help="print the files to transfer instead of downloading")
    args = ap.parse_args()

    repo = REPO_TEMPLATE.format(model=args.model)
    files = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt",
             "preprocessor_config.json"]

    if args.manifest:
        print(f"# Fetch these from https://huggingface.co/{repo}/resolve/main/")
        for f in files:
            print(f"https://huggingface.co/{repo}/resolve/main/{f}")
        print("\n# Then place them in one directory and set STT_MODEL_DIR to it.")
        return

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit("huggingface_hub not installed — run this inside backend/voice's env")

    path = snapshot_download(repo_id=repo, local_dir=args.dir, allow_patterns=files)
    print(f"Model ready: {path}")


if __name__ == "__main__":
    main()
