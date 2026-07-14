#!/usr/bin/env python
"""Probe whether an OpenAI-compatible provider accepts image input via the API.

CityA's vision pipeline emits standard OpenAI `image_url` content blocks. Not
every OpenAI-compatible endpoint actually supports them: llama-server (Qwen-VL)
does; the DeepSeek API (as of 2026-07) rejects `image_url` outright even though
the underlying V4 model is multimodal in their web app. Run this before assuming
a provider is a drop-in replacement for vision.

It builds a KNOWN image locally (red background, white text "VISION-OK 42"),
sends it as a base64 image_url, and reports whether the model actually read it.

Usage:
    MODEL_BASE_URL=https://api.deepseek.com \
    MODEL_API_KEY=sk-... \
    MODEL_NAME=deepseek-v4-flash \
    env -u VIRTUAL_ENV uv run python scripts/check_provider_vision.py

    # optional: --thinking enabled|disabled   (sends {"thinking":{"type":...}})
    # optional: --model NAME (overrides MODEL_NAME)

Exit code 0 = vision works; non-zero = rejected / unread / error.
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import sys

from openai import APIStatusError, OpenAI
from PIL import Image, ImageDraw

TEXT_IN_IMAGE = "VISION-OK 42"


def make_test_image() -> str:
    """Red 512x256 PNG stamped with white TEXT_IN_IMAGE; returns a data URL."""
    img = Image.new("RGB", (512, 256), (200, 30, 30))
    stamp = Image.new("RGB", (256, 64), (200, 30, 30))
    ImageDraw.Draw(stamp).text((4, 20), TEXT_IN_IMAGE, fill=(255, 255, 255))
    img.paste(stamp.resize((480, 120)), (16, 70))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=os.environ.get("MODEL_NAME"))
    ap.add_argument("--thinking", choices=["enabled", "disabled"], default=None)
    args = ap.parse_args()

    base_url = os.environ.get("MODEL_BASE_URL")
    api_key = os.environ.get("MODEL_API_KEY")
    if not (base_url and api_key and args.model):
        print("Set MODEL_BASE_URL, MODEL_API_KEY, and MODEL_NAME (or --model).")
        return 2

    client = OpenAI(base_url=base_url, api_key=api_key)
    print(f"model={args.model}  base_url={base_url}  thinking={args.thinking}")

    kwargs: dict = dict(
        model=args.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What background color is this image, "
                     "and what exact text is written on it?"},
                    {"type": "image_url", "image_url": {"url": make_test_image()}},
                ],
            }
        ],
    )
    if args.thinking:
        kwargs["extra_body"] = {"thinking": {"type": args.thinking}}

    try:
        r = client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        print(f"HTTP {exc.status_code}: provider rejected the request.")
        print(str(getattr(exc, "message", exc))[:300])
        print("\nRESULT: vision NOT supported via this API.")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1

    reply = (r.choices[0].message.content or "").strip()
    low = reply.lower()
    saw_color = "red" in low
    saw_text = "42" in low or "vision-ok" in low
    print(f"reply: {reply[:200]!r}")
    print(f"sees color(red)={saw_color}  sees text={saw_text}")

    if saw_color and saw_text:
        print("\nRESULT: PASS — vision works via the API.")
        return 0
    if reply:
        print("\nRESULT: PARTIAL — image accepted but not clearly read (see reply).")
        return 1
    print("\nRESULT: FAIL — empty reply.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
