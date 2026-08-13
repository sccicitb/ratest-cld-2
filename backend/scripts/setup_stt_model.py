#!/usr/bin/env python
"""Prefetch the faster-whisper model so prod never needs HuggingFace at runtime.

Prod is air-gapped. faster-whisper downloads CTranslate2 weights on first use,
which on that host means a hang and then a failed transcription. Run this once
per deploy -- or, with no outbound internet, run --manifest on a connected
machine, copy the listed files, and set STT_MODEL_DIR on the target.

The repo id is resolved through faster-whisper's own `_MODELS` table rather than
string-templated here. That is not a style preference: model names do not map to
one publisher. `large-v3-turbo` lives at `mobiuslabsgmbh/faster-whisper-...`,
not `Systran/...`, and a cache populated under the wrong id would not be found
by the runtime even if the download itself succeeded. Prefetch and runtime must
agree on the id, so there is exactly one source of truth for it.

Usage:
    uv run python scripts/setup_stt_model.py
    uv run python scripts/setup_stt_model.py --model large-v3 --manifest
"""
from __future__ import annotations

import argparse
import os
import sys

# faster-whisper's own allow_patterns (utils.download_model) ends with the glob
# `vocabulary.*` because publishers differ: Systran/faster-whisper-large-v3
# ships only vocabulary.json, mobiuslabsgmbh ships both .json and .txt. The
# manifest needs a concrete filename, and .json is the one present in every
# CT2 Whisper repo we support -- .txt would 404 on large-v3.
#
# `snapshot_download` silently ignores patterns that match nothing, so getting
# this wrong does not fail loudly; it just omits the vocabulary.
MANIFEST_FILES = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.json",
)


def resolve_repo_id(model: str) -> str:
    """Map an STT_MODEL name to the HuggingFace repo the runtime will look up.

    Mirrors `faster_whisper.utils.download_model`: an explicit ``owner/name``
    passes through untouched, anything else must be a key in `_MODELS`.
    """
    try:
        from faster_whisper.utils import _MODELS
    except ImportError:
        sys.exit("faster-whisper not installed — run this inside backend/voice's env")

    if "/" in model:
        return model
    repo = _MODELS.get(model)
    if repo is None:
        sys.exit(
            f"unknown STT_MODEL {model!r} — expected one of: "
            + ", ".join(sorted(_MODELS))
            + "\n(or an explicit HuggingFace repo id like 'owner/name')"
        )
    return repo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("STT_MODEL", "large-v3-turbo"))
    ap.add_argument("--dir", default=os.environ.get("STT_MODEL_DIR") or None)
    ap.add_argument("--manifest", action="store_true",
                    help="print the files to transfer instead of downloading")
    args = ap.parse_args()

    repo = resolve_repo_id(args.model)

    if args.manifest:
        print(f"# {args.model} -> https://huggingface.co/{repo}")
        for f in MANIFEST_FILES:
            print(f"https://huggingface.co/{repo}/resolve/main/{f}")
        print("\n# Then place them in one directory and set STT_MODEL_DIR to it.")
        return

    # download_model owns repo resolution, allow_patterns and the cache layout,
    # so a cache filled here is exactly the cache WhisperModel reads.
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        sys.exit("faster-whisper not installed — run this inside backend/voice's env")

    path = download_model(args.model, output_dir=args.dir)
    print(f"Model ready: {path}")


if __name__ == "__main__":
    main()
