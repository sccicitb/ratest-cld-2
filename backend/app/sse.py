"""Server-Sent Events framing helper (§8.3).

A single function that turns a JSON-able dict into a wire-ready SSE frame:
`data: <compact json>\n\n`. Used by the upload/reindex endpoints to stream
`chunk_progress` / `file_resolved` / `done` events.
"""
from __future__ import annotations

import json


def sse(data: dict) -> bytes:
    """Encode `data` as a single SSE `data:` frame, compact JSON, UTF-8 bytes."""
    payload = json.dumps(data)
    return f"data: {payload}\n\n".encode("utf-8")
