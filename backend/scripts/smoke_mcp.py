"""Smoke test: verify MCP wiring against the live satudata-mcp.

NOT a CI test — run manually when satudata-mcp is up at localhost:8800.

Usage (from backend/):
    env -u VIRTUAL_ENV uv run python scripts/smoke_mcp.py

What it does:
1. Builds an MCPManager with satudata-garut enabled at localhost:8800.
2. Connects and prints the namespaced tool list.
3. Calls search_datasets(query="penduduk") and prints the result.
4. (Full-loop) POSTs a chat message to the running app at localhost:8000
   and confirms the SSE stream shows calling_tool → result → done.
   The full-loop step requires: uvicorn running with SATUDATA_ENABLED=true
   or mcp.yaml temporarily flipped.
"""
from __future__ import annotations

import asyncio
import json
import sys

# ── Step 4 full-loop is optional — httpx may not be installed ──────────────
try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

from app.config import settings
from app.mcp.config import MCPConfig, MCPServerConfig, MCPAuth
from app.mcp.manager import MCPManager
from app.tools.context import ToolContext
from app.tools.registry import ToolError

# satudata-garut can take up to ~90s on first call — raise timeout for smoke only.
settings.mcp_tool_timeout_seconds = 120


_SATUDATA_URL = "http://localhost:8800/mcp"
_APP_URL = "http://localhost:8000"


def _make_config() -> MCPConfig:
    return MCPConfig(
        mcp_servers=[
            MCPServerConfig(
                name="satudata-garut",
                transport="streamable-http",
                url=_SATUDATA_URL,
                auth=MCPAuth(type="none"),
                enabled=True,
                allowed_tools=[],
            )
        ]
    )


async def smoke_mcp_manager() -> list:
    """Connect MCPManager and return the tool list."""
    config = _make_config()
    manager = MCPManager(config)
    try:
        tools = await manager.connect_all()
        print(f"\n[smoke] MCPManager connected — {len(tools)} tool(s):")
        for t in tools:
            print(f"  • {t.name}")
        return tools, manager
    except Exception as exc:
        print(f"[smoke] FAILED to connect: {exc}", file=sys.stderr)
        await manager.aclose()
        raise


async def smoke_tool_call(tools: list, manager: MCPManager) -> None:
    """Call search_datasets via the wrapped MCPTool.execute."""
    echo_tool = next((t for t in tools if "search_datasets" in t.name), None)
    if echo_tool is None:
        print("[smoke] search_datasets not found in tool list!", file=sys.stderr)
        return

    # ToolContext fields used by search_kb only — MCP tools ignore ctx
    ctx = ToolContext(
        user_id="smoke-user",
        session_id="smoke-session",
        db=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
    )

    print(f"\n[smoke] Calling {echo_tool.name}(query='penduduk') ...")
    try:
        result = await echo_tool.execute({"query": "penduduk"}, ctx)
        print("[smoke] Result (first 500 chars):")
        print(result[:500])
    except ToolError as exc:
        print(f"[smoke] ToolError: {exc}", file=sys.stderr)
    finally:
        await manager.aclose()


async def smoke_full_loop() -> None:
    """POST a chat to the running app and check SSE for calling_tool events."""
    if not _HAS_HTTPX:
        print("\n[smoke/full-loop] httpx not installed — skipping full-loop smoke.")
        return

    print(f"\n[smoke/full-loop] Checking app at {_APP_URL}/api/health ...")
    async with httpx.AsyncClient(base_url=_APP_URL, timeout=10) as hc:
        try:
            r = await hc.get("/api/health")
            if r.status_code != 200:
                print(f"[smoke/full-loop] App not healthy ({r.status_code}) — skipping.")
                return
        except Exception as exc:
            print(f"[smoke/full-loop] App unreachable ({exc}) — skipping.")
            return

        print("[smoke/full-loop] App is up. Registering a demo user and session ...")
        # Register / login
        reg = await hc.post(
            "/api/auth/register",
            json={"email": "smoke@example.com", "password": "smoke1234", "displayName": "Smoke"},
        )
        if reg.status_code not in (200, 201, 409):
            print(f"[smoke/full-loop] Register failed: {reg.status_code} {reg.text}")
            return
        login = await hc.post(
            "/api/auth/login",
            json={"email": "smoke@example.com", "password": "smoke1234"},
        )
        if login.status_code != 200:
            print(f"[smoke/full-loop] Login failed: {login.status_code} {login.text}")
            return
        token = login.json()["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create session
        sess = await hc.post("/api/sessions", headers=headers)
        if sess.status_code != 201:
            print(f"[smoke/full-loop] Session creation failed: {sess.status_code}")
            return
        sid = sess.json()["id"]
        print(f"[smoke/full-loop] Session: {sid}")

        # POST chat — long timeout since model inference can be slow
        print("[smoke/full-loop] Sending chat message ...")
        async with hc.stream(
            "POST",
            f"/api/sessions/{sid}/chat",
            headers={**headers, "Accept": "text/event-stream"},
            json={"message": "search the Garut open-data catalog for population datasets"},
            timeout=120,
        ) as resp:
            if resp.status_code != 200:
                print(f"[smoke/full-loop] Chat failed: {resp.status_code}")
                return

            calling_tool_seen = False
            done_seen = False
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:"):].strip())
                etype = event.get("type")
                if etype == "step" and event.get("step") == "calling_tool":
                    status = event.get("status")
                    tool_name = event.get("toolName", "")
                    print(f"[smoke/full-loop]   calling_tool {status}: {tool_name}")
                    if status == "active":
                        calling_tool_seen = True
                elif etype == "token":
                    print(f"[smoke/full-loop]   token: {event.get('content','')[:80]}")
                elif etype == "done":
                    done_seen = True
                    print("[smoke/full-loop]   done ✓")
                    break

            if calling_tool_seen and done_seen:
                print("[smoke/full-loop] PASS — calling_tool + done observed.")
            elif done_seen:
                print("[smoke/full-loop] PARTIAL — done seen but no calling_tool (model may not have called the tool).")
            else:
                print("[smoke/full-loop] INCOMPLETE — stream ended without done.")


async def main() -> None:
    print("=== satudata-mcp smoke test ===")
    print(f"Target: {_SATUDATA_URL}")

    tools, manager = await smoke_mcp_manager()
    await smoke_tool_call(tools, manager)
    await smoke_full_loop()

    print("\n=== smoke done ===")


if __name__ == "__main__":
    asyncio.run(main())
