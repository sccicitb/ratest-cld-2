"""Smoke test: verify execute_code tool + sandbox service end-to-end.

NOT a CI test — run manually after building the sandbox image and starting
the code-exec service.

Prereqs:
    docker build -t rag-sandbox backend/sandbox/runner
    env -u VIRTUAL_ENV uv run uvicorn sandbox.service.main:app --port 8001

Usage (from backend/):
    PYTHONPATH=. env -u VIRTUAL_ENV uv run python scripts/smoke_execute_code.py

Level A (always): call ExecuteCode directly with code that fetches a public
    CSV via pandas, then a second call reusing the loaded DataFrame (state
    persistence across calls in the same session).

Level B (optional — needs satudata-mcp at :8800 AND app at :8000):
    boot the app with MCP + execute_code, POST a chat asking to get a Garut
    population dataset and report the row count; confirm SSE shows
    get_dataset_data → execute_code → text answer.
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

from app.config import settings
from app.tools.builtin.execute_code import ExecuteCode
from app.tools.context import ToolContext

_APP_URL = "http://localhost:8000"
_SANDBOX_URL = settings.code_exec_url  # default http://localhost:8001
_SMOKE_SESSION = "smoke-session-execute-code-01"

# A small public CSV that is reliably available.
_CSV_URL = "https://people.sc.fsu.edu/~jburkardt/data/csv/airtravel.csv"


# ---------------------------------------------------------------------------
# Level A — direct tool + sandbox
# ---------------------------------------------------------------------------


async def smoke_level_a() -> bool:
    """Call ExecuteCode with pandas CSV fetch + persistence check.

    Returns True on success.
    """
    print("\n=== Level A: direct ExecuteCode smoke ===")
    print(f"Sandbox: {_SANDBOX_URL}")

    tool = ExecuteCode()
    ctx = ToolContext(
        user_id="smoke-user",
        session_id=_SMOKE_SESSION,
        db=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
    )

    # Call 1: fetch CSV and load into df, print shape
    code1 = (
        f"import pandas as pd\n"
        f"df = pd.read_csv('{_CSV_URL}')\n"
        f"print('shape:', df.shape)\n"
        f"print(df.head(3).to_string())\n"
    )
    print(f"\n[A/call-1] code:\n{code1.strip()}\n")
    try:
        result1 = await tool.execute({"code": code1}, ctx)
        print(f"[A/call-1] result:\n{result1}\n")
    except Exception as exc:
        print(f"[A/call-1] FAILED: {exc}", file=sys.stderr)
        return False

    if "shape" not in result1:
        print("[A/call-1] WARN: 'shape' not in output — sandbox may have no egress or pandas missing.")

    # Call 2: reuse df from previous call (persistence test)
    code2 = "print('row count from persisted df:', len(df))\n"
    print(f"[A/call-2] code:\n{code2.strip()}\n")
    try:
        result2 = await tool.execute({"code": code2}, ctx)
        print(f"[A/call-2] result:\n{result2}\n")
    except Exception as exc:
        print(f"[A/call-2] FAILED (persistence test): {exc}", file=sys.stderr)
        return False

    if "row count from persisted df" in result2:
        print("[A] PASS — fetch + pandas + state persistence confirmed.")
    else:
        print("[A] PARTIAL — second call succeeded but df persistence unclear.")

    # Cleanup
    try:
        async with httpx.AsyncClient(timeout=5) as hc:
            await hc.delete(f"{_SANDBOX_URL}/sessions/{_SMOKE_SESSION}")
        print(f"[A] Session {_SMOKE_SESSION} cleaned up.")
    except Exception:
        pass

    return True


# ---------------------------------------------------------------------------
# Level B — full-loop via running app (optional)
# ---------------------------------------------------------------------------


async def smoke_level_b() -> bool:
    """POST a chat about Garut population data; confirm tool chain in SSE.

    Returns True if full chain observed, None-ish if app/mcp not up.
    """
    print("\n=== Level B: full-loop (app + satudata-mcp) ===")
    print(f"App: {_APP_URL}")

    async with httpx.AsyncClient(base_url=_APP_URL, timeout=15) as hc:
        try:
            r = await hc.get("/api/health")
            if r.status_code != 200:
                print(f"[B] App not healthy ({r.status_code}) — skipping Level B.")
                return False
        except Exception as exc:
            print(f"[B] App unreachable ({exc}) — skipping Level B.")
            return False

        # Register / login
        await hc.post(
            "/api/auth/register",
            json={"email": "smoke-exec@example.com", "password": "smoke1234", "displayName": "SmokeExec"},
        )
        login = await hc.post(
            "/api/auth/login",
            json={"email": "smoke-exec@example.com", "password": "smoke1234"},
        )
        if login.status_code != 200:
            print(f"[B] Login failed: {login.status_code} — skipping.")
            return False
        token = login.json()["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        sess = await hc.post("/api/sessions", headers=headers)
        if sess.status_code != 201:
            print(f"[B] Session create failed: {sess.status_code} — skipping.")
            return False
        sid = sess.json()["id"]
        print(f"[B] Session: {sid}")

        message = (
            "Use satudata to get the data for a Garut population dataset "
            "and tell me how many rows it has."
        )
        print(f"[B] Sending: {message!r} ...")

    # Use a new client with a longer timeout for the streaming chat call
    tool_chain: list[str] = []
    done_seen = False
    final_answer = ""

    async with httpx.AsyncClient(base_url=_APP_URL, timeout=180) as hc2:
        async with hc2.stream(
            "POST",
            f"/api/sessions/{sid}/chat",
            headers={**headers, "Accept": "text/event-stream"},
            json={"message": message},
        ) as resp:
            if resp.status_code != 200:
                print(f"[B] Chat failed: {resp.status_code} — skipping.")
                return False

            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:"):].strip())
                etype = event.get("type")
                if etype == "step" and event.get("step") == "calling_tool":
                    status = event.get("status")
                    tool_name = event.get("toolName", "")
                    if status == "active":
                        tool_chain.append(tool_name)
                        print(f"[B]   calling_tool active: {tool_name}")
                elif etype == "token":
                    final_answer += event.get("content", "")
                elif etype == "done":
                    done_seen = True
                    print("[B]   done ✓")
                    break

    print(f"\n[B] Tool chain: {tool_chain}")
    print(f"[B] Final answer (first 300 chars): {final_answer[:300]}")

    get_data_seen = any("get_dataset_data" in t for t in tool_chain)
    exec_seen = any("execute_code" in t for t in tool_chain)

    if get_data_seen and exec_seen and done_seen:
        print("[B] PASS — get_dataset_data → execute_code → answer observed.")
        return True
    elif done_seen:
        print("[B] PARTIAL — done seen but expected tool chain incomplete.")
        print(f"       get_dataset_data: {get_data_seen}, execute_code: {exec_seen}")
        return False
    else:
        print("[B] INCOMPLETE — stream ended without done.")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("=== execute_code smoke test ===")

    a_ok = await smoke_level_a()

    b_ok = await smoke_level_b()

    print("\n=== summary ===")
    print(f"Level A: {'PASS' if a_ok else 'FAIL'}")
    print(f"Level B: {'PASS' if b_ok else 'SKIP/FAIL'}")

    if not a_ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
