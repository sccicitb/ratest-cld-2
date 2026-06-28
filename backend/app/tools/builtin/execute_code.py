"""`execute_code` — runs Python in the session's persistent sandbox (§13).

The session_id comes exclusively from `ToolContext` (server-set, trusted).
It is NEVER read from model-supplied `args`. The schema below exposes no
session/scope parameter to the model.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.tools.context import ToolContext
from app.tools.registry import ToolError

log = logging.getLogger(__name__)

_STDOUT_MAX = 4000


class ExecuteCode:
    name = "execute_code"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": (
                    "Run Python in a persistent sandbox (pandas/numpy/matplotlib "
                    "preinstalled). Use it to fetch and analyze a `download_url` "
                    "returned by another tool (e.g. `pd.read_csv(url)`), compute, "
                    "or transform data. State persists across calls in the same "
                    "conversation (a loaded DataFrame stays available)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python code to execute in the sandbox.",
                        },
                    },
                    "required": ["code"],
                },
            },
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        session_id = ctx.session_id
        if not session_id:
            raise ToolError("execute_code requires a session")

        code = args.get("code")
        if not code or not isinstance(code, str):
            raise ToolError("execute_code requires a non-empty 'code' string")

        url = f"{settings.code_exec_url}/sessions/{session_id}/execute"
        try:
            async with httpx.AsyncClient(timeout=settings.code_exec_timeout_seconds) as hc:
                resp = await hc.post(url, json={"code": code})
        except httpx.ConnectError as exc:
            raise ToolError(f"code sandbox unavailable: {exc}") from exc

        if resp.status_code >= 400:
            raise ToolError(
                f"code sandbox error {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        stdout: str = data.get("stdout") or ""
        error: str | None = data.get("error")
        artifacts: list = data.get("artifacts") or []

        parts: list[str] = []
        if stdout:
            if len(stdout) > _STDOUT_MAX:
                stdout = (
                    stdout[:_STDOUT_MAX]
                    + f"\n[output truncated — {len(stdout)} chars total]"
                )
            parts.append(stdout)
        if error:
            parts.append(f"Error: {error}")
        if artifacts:
            parts.append(f"[{len(artifacts)} chart(s) generated]")

        return "\n".join(parts) if parts else "(no output)"
