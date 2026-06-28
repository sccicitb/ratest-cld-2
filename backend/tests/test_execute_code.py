"""Unit tests for ExecuteCode tool (Stage 10, §13).

HTTP calls are mocked via unittest.mock — no network required, no respx needed.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.tools.builtin.execute_code import ExecuteCode, _STDOUT_MAX
from app.tools.context import ToolContext
from app.tools.registry import ToolError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(session_id: str | None = "sess-abc") -> ToolContext:
    return ToolContext(
        user_id="u1",
        session_id=session_id,
        db=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
    )


def _fake_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    resp.text = str(body)
    return resp


def _patch_client(resp: MagicMock):
    """Context manager: patches httpx.AsyncClient so .post() returns resp."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return patch("app.tools.builtin.execute_code.httpx.AsyncClient", return_value=mock_ctx)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_shape():
    tool = ExecuteCode()
    s = tool.schema()
    fn = s["function"]
    assert s["type"] == "function"
    assert fn["name"] == "execute_code"
    props = fn["parameters"]["properties"]
    assert set(props.keys()) == {"code"}
    assert fn["parameters"]["required"] == ["code"]
    # Security: no scope-controlling params exposed to the model.
    assert "session_id" not in props
    assert "user_id" not in props


# ---------------------------------------------------------------------------
# ctx.session_id is None → ToolError without HTTP call
# ---------------------------------------------------------------------------


def test_no_session_raises_tool_error_without_http():
    tool = ExecuteCode()
    ctx = _ctx(session_id=None)
    with patch("app.tools.builtin.execute_code.httpx.AsyncClient") as mock_cls:
        with pytest.raises(ToolError, match="requires a session"):
            asyncio.run(tool.execute({"code": "print(1)"}, ctx))
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_stdout():
    tool = ExecuteCode()
    ctx = _ctx("my-session")
    resp = _fake_response(200, {"stdout": "Hello world\n", "error": None, "artifacts": []})

    with _patch_client(resp) as mock_cls:
        result = asyncio.run(tool.execute({"code": "print('Hello world')"}, ctx))

    assert "Hello world" in result
    # Verify the POST was made to the correct URL including the session_id from ctx
    mock_cls.assert_called_once()
    instance = mock_cls.return_value.__aenter__.return_value
    call_args = instance.post.call_args
    assert "my-session" in call_args[0][0]
    assert call_args[1]["json"]["code"] == "print('Hello world')"


def test_session_id_comes_from_ctx_not_args():
    """Even if args carries a bogus session_id, ctx.session_id is used."""
    tool = ExecuteCode()
    ctx = _ctx("real-session")
    resp = _fake_response(200, {"stdout": "ok", "error": None, "artifacts": []})

    with _patch_client(resp) as mock_cls:
        asyncio.run(
            tool.execute(
                {"code": "print('x')", "session_id": "bogus-session"},
                ctx,
            )
        )

    instance = mock_cls.return_value.__aenter__.return_value
    url = instance.post.call_args[0][0]
    assert "real-session" in url
    assert "bogus-session" not in url


# ---------------------------------------------------------------------------
# Error field surfaced
# ---------------------------------------------------------------------------


def test_error_field_appended():
    tool = ExecuteCode()
    ctx = _ctx()
    resp = _fake_response(
        200,
        {"stdout": "partial output", "error": "NameError: x is not defined", "artifacts": []},
    )

    with _patch_client(resp):
        result = asyncio.run(tool.execute({"code": "print(x)"}, ctx))

    assert "partial output" in result
    assert "NameError" in result


# ---------------------------------------------------------------------------
# Artifacts note appended
# ---------------------------------------------------------------------------


def test_artifacts_note_appended():
    tool = ExecuteCode()
    ctx = _ctx()
    resp = _fake_response(
        200,
        {"stdout": "done", "error": None, "artifacts": ["plot1.png", "plot2.png"]},
    )

    with _patch_client(resp):
        result = asyncio.run(tool.execute({"code": "import matplotlib; ..."}, ctx))

    assert "2 chart(s) generated" in result


# ---------------------------------------------------------------------------
# Stdout truncation
# ---------------------------------------------------------------------------


def test_long_stdout_truncated():
    tool = ExecuteCode()
    ctx = _ctx()
    long_out = "x" * (_STDOUT_MAX + 500)
    resp = _fake_response(200, {"stdout": long_out, "error": None, "artifacts": []})

    with _patch_client(resp):
        result = asyncio.run(tool.execute({"code": "print('x'*5000)"}, ctx))

    assert "truncated" in result
    assert len(result) < len(long_out)


# ---------------------------------------------------------------------------
# Service 500 / connection refused → ToolError
# ---------------------------------------------------------------------------


def test_service_500_raises_tool_error():
    tool = ExecuteCode()
    ctx = _ctx()
    resp = _fake_response(500, {"detail": "container crashed"})

    with _patch_client(resp):
        with pytest.raises(ToolError, match="code sandbox error 500"):
            asyncio.run(tool.execute({"code": "raise RuntimeError()"}, ctx))


def test_connection_refused_raises_tool_error():
    tool = ExecuteCode()
    ctx = _ctx()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.tools.builtin.execute_code.httpx.AsyncClient", return_value=mock_ctx):
        with pytest.raises(ToolError, match="code sandbox unavailable"):
            asyncio.run(tool.execute({"code": "print(1)"}, ctx))


# ---------------------------------------------------------------------------
# Empty code → ToolError (no HTTP call)
# ---------------------------------------------------------------------------


def test_empty_code_raises_tool_error():
    tool = ExecuteCode()
    ctx = _ctx()
    with patch("app.tools.builtin.execute_code.httpx.AsyncClient") as mock_cls:
        with pytest.raises(ToolError):
            asyncio.run(tool.execute({"code": ""}, ctx))
        mock_cls.assert_not_called()
