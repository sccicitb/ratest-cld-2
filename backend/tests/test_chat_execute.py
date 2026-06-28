"""Chat route tests for execute_code tool wiring (Stage 10, §13).

Exercises two scenarios:
1. Model emits execute_code tool_call → SSE has calling_tool toolName=="execute_code"
   → ends done (service mocked via httpx patch).
2. Sandbox returns error text → tool-result error fed back, stream still HTTP 200
   ending in done (no 500).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.chat.client import ModelChunk
from app.chat.routes import get_model_client
from app.main import app


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeModelClient:
    """Scripted sequence of ModelChunk lists — one per .stream() call."""

    def __init__(self, script: list[list[ModelChunk]]):
        self._script = list(script)

    async def stream(self, messages, tools):
        chunks = self._script.pop(0) if self._script else []
        for chunk in chunks:
            yield chunk


def _fake_sandbox_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    resp.text = str(body)
    return resp


def _patch_sandbox(resp: MagicMock):
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return patch("app.tools.builtin.execute_code.httpx.AsyncClient", return_value=mock_ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def _create_session(client, auth_headers) -> str:
    r = client.post("/api/sessions", headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_execute_code_tool_called_emits_calling_tool_events(client, auth_headers):
    """Model calls execute_code → loop emits calling_tool events, ends done."""
    from qdrant_client import QdrantClient
    from app.kb.routes import get_qdrant as _get_qdrant

    sid = _create_session(client, auth_headers)

    script = [
        [
            ModelChunk(
                type="tool_call",
                id="tc-exec-1",
                name="execute_code",
                arguments={"code": "print(42)"},
            )
        ],
        [ModelChunk(type="text", text="The result is 42.")],
    ]

    sandbox_resp = _fake_sandbox_response(
        200, {"stdout": "42\n", "error": None, "artifacts": []}
    )

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(script)
    app.dependency_overrides[_get_qdrant] = lambda: QdrantClient(location=":memory:")
    try:
        with _patch_sandbox(sandbox_resp):
            r = client.post(
                f"/api/sessions/{sid}/chat",
                headers=auth_headers,
                json={"message": "run some code"},
            )
    finally:
        app.dependency_overrides.pop(get_model_client, None)
        app.dependency_overrides.pop(_get_qdrant, None)

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)

    calling_tool_events = [
        e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
    ]
    assert len(calling_tool_events) == 2, calling_tool_events
    active, complete = calling_tool_events
    assert active["status"] == "active"
    assert active["toolName"] == "execute_code"
    assert complete["status"] == "complete"

    assert events[-1]["type"] == "done"


def test_sandbox_error_feeds_back_stream_still_200(client, auth_headers):
    """Sandbox returns 500 → ToolError → tool-result fed back; stream ends done (no 500)."""
    from qdrant_client import QdrantClient
    from app.kb.routes import get_qdrant as _get_qdrant

    sid = _create_session(client, auth_headers)

    script = [
        [
            ModelChunk(
                type="tool_call",
                id="tc-exec-err",
                name="execute_code",
                arguments={"code": "raise RuntimeError('boom')"},
            )
        ],
        [ModelChunk(type="text", text="I encountered a sandbox error.")],
    ]

    error_resp = _fake_sandbox_response(500, {"detail": "container crashed"})

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(script)
    app.dependency_overrides[_get_qdrant] = lambda: QdrantClient(location=":memory:")
    try:
        with _patch_sandbox(error_resp):
            r = client.post(
                f"/api/sessions/{sid}/chat",
                headers=auth_headers,
                json={"message": "run broken code"},
            )
    finally:
        app.dependency_overrides.pop(get_model_client, None)
        app.dependency_overrides.pop(_get_qdrant, None)

    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)

    # calling_tool pair must exist even on error
    calling_tool_events = [
        e for e in events if e["type"] == "step" and e["step"] == "calling_tool"
    ]
    assert len(calling_tool_events) == 2

    assert events[-1]["type"] == "done"
