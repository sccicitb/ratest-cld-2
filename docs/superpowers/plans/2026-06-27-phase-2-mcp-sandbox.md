# Phase 2 — MCP, Sandbox & Deployment — Staged Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (one stage per branch, merged `--no-ff`, branch kept). Steps use checkbox (`- [ ]`) syntax.
>
> Continues [`2026-06-26-backend-rag-core.md`](./2026-06-26-backend-rag-core.md). The RAG core (Stages 0–6 + OCR) and the frontend integration are **DONE**. This plan covers the optional Phase 2 work, sequenced by the user's priority: **modular MCP integration first.**

**Goal:** Implement Phase 2 of [`docs/BACKEND_SPEC.md`](../../BACKEND_SPEC.md) (status: LOCKED): external tools via MCP (§12.2), the `execute_code` sandbox (§13), and deployment.

## Global Constraints (inherited)

All [core-plan Global Constraints](./2026-06-26-backend-rag-core.md) still apply. Phase-2 additions:

- **Tool registry is the only extension point.** Every tool — native or MCP — exposes `name`, `schema()` (OpenAI function def), `execute(args, ctx)`. **Adding a tool changes the registry, not the loop** (§12.2). `search_knowledge_base` stays hand-written native (keeps server-side scope injection) — **never** MCP-ified.
- **MCP transport: `streamable-http` only** (no stdio server-side). Backend connects each server's `/mcp` endpoint via the official `mcp` SDK — **never** a vendor Messages-API connector.
- **Config-driven:** adding an MCP server is **config, not code** (YAML `mcp_servers`, per spec §12.2). Credentials come from server-side env, never exposed to the model.
- **Failure isolation:** a server down / timing out / auth-failing is caught → tool-result error (or its tools dropped for the turn) → **never crashes the loop**.
- **Large payloads bypass the model:** tools return small results inline; large results return a **`download_url`** the sandbox fetches directly (§12.2 house convention; §13).
- **Sandbox is walled (§10/§13):** ephemeral container, isolated Docker network, egress allowlist (MCP fileserver + internet), **never** Qdrant/app-DB/app-host; non-root; CPU/mem/time/PID caps; no secrets.

## Epic map & weights (1 pt ≈ half-day)

| Epic | Stages | Weight | Status |
|---|---|---|---|
| **A. Modular MCP** | 7, 8 | **8** | ← FIRST |
| B. `execute_code` sandbox | 9, 10 | 13 | after A |
| C. Deployment | 11 | 5 | last |

**Dependency:** MCP tools returning **small inline results** (satudata `search_datasets`, `get_dataset_info`) work fully **without** the sandbox. Only the large-data path (`get_dataset_data` → `download_url` → `execute_code`) needs Epic B. So Epic A ships and is useful standalone.

---

## Epic A — Modular MCP integration

### Stage 7 — MCP config + `MCPManager` (weight 5, L) — branch `stage-7-mcp-core`

**Deliverable:** A config-driven manager that connects MCP servers and exposes their tools through the existing registry contract, namespaced and failure-isolated. No chat-loop wiring yet (Stage 8).

Files: `app/mcp/config.py` (new), `app/mcp/manager.py` (new), `app/tools/mcp/wrapper.py` (new), `mcp.yaml` (new, at backend root), `app/config.py` (add `mcp_config_path`), `pyproject.toml` (already has `mcp>=1.0`; install it).

- [ ] **7.1 Config schema + loader.** Pydantic models `MCPServerConfig { name, transport, url, auth: {type: none|bearer, token_env?}, enabled, allowed_tools: list[str] }` and `MCPConfig { mcp_servers: [...] }`. Loader reads YAML from `settings.mcp_config_path` (default `./mcp.yaml`); missing file → empty list (MCP simply off). Ship a `mcp.yaml` matching the spec's locked example (satudata-garut, disabled by default).
- [ ] **7.2 MCP client session.** `app/mcp/manager.py`: per-server connect via the `mcp` SDK `streamablehttp_client(url, headers)` → `ClientSession`; `initialize()`; resolve bearer token from `token_env` when `auth.type == bearer`. One helper that yields a live session (async context).
- [ ] **7.3 Tool wrapper.** `app/tools/mcp/wrapper.py`: `MCPTool` implementing the registry `Tool` protocol — `name = f"{server}.{tool_name}"`, `schema()` maps the MCP tool's `inputSchema` to the OpenAI function shape, `execute(args, ctx)` calls `session.call_tool(tool_name, args)` and returns the text content (joins text parts; surfaces structured/`download_url` payloads verbatim). Scope from `ctx` is **not** passed to MCP tools (those are external; only `search_kb` gets scope).
- [ ] **7.4 Manager: connect/list/wrap/namespace.** `MCPManager.connect_all()` iterates enabled servers, `list_tools()`, filters by `allowed_tools` (empty = all), wraps each as `MCPTool`. Returns `list[Tool]`. Per-server try/except → log + skip that server (failure isolation at startup). `aclose()` tears sessions down.
- [ ] **7.5 Tests.** Unit-test config loader (valid/missing/disabled, allowed_tools filter). Manager/wrapper tests against the `mcp` SDK **in-memory** server (fake tools): namespacing (`fake.echo`), schema mapping, `execute` round-trip, one server raising on connect doesn't drop the others.

**DoD:** `MCPManager.connect_all()` returns wrapped, namespaced, registry-compatible tools from a YAML config; a broken server is isolated; tests green; `mcp` extra installed.

### Stage 8 — Loop/registry wiring + lifespan + real smoke (weight 3, M) — branch `stage-8-mcp-wiring`

**Deliverable:** MCP tools are live in the chat loop, connected once at app startup, with per-turn failure isolation. Verified end-to-end against the real **satudata-mcp**.

Files: `app/main.py` (lifespan), `app/chat/routes.py` (`_build_registry` merges MCP tools), `app/mcp/manager.py` (singleton accessor), tests.

- [ ] **8.1 Lifespan startup.** FastAPI `lifespan`: build the `MCPManager`, `await connect_all()` once, stash the wrapped tools (+ manager) on app state; `aclose()` on shutdown. Connect failures log but never block boot.
- [ ] **8.2 Registry merge.** `_build_registry()` (or a DI dep) returns native `search_knowledge_base` **+** the startup-loaded MCP tools. Per-request scope injection unchanged (only `search_kb` reads `ctx` scope). Keep the registry per-request but source MCP tools from app state (cheap — the sessions are reused/managed by the manager).
- [ ] **8.3 Per-turn failure isolation.** A tool `execute` that raises/times out → returns a tool-result error string the loop feeds back to the model (already the `ToolError`/`_run_tool_call` path) — confirm MCP errors flow through it; add a timeout around `call_tool`.
- [ ] **8.4 Tests.** Route-level: with a fake MCP server registered, the loop offers the namespaced tool, calls it, streams `calling_tool` events with the right `toolName`, and a failing MCP tool yields a tool-result error (not a 500/stream crash).
- [ ] **8.5 Real smoke (satudata-mcp).** With the user's reachable `satudata-mcp` (streamable-http) enabled in `mcp.yaml`: boot the app, send a chat that triggers a satudata tool, confirm the tool call + result stream end-to-end (like the llama-server smoke). **Prereq:** collect the satudata-mcp URL + auth from the user at stage start.

**DoD:** A chat turn can call a satudata tool via MCP, results stream back, a down server doesn't break chat; native `search_kb` unaffected; tests green; real smoke passes.

---

## Epic B — `execute_code` sandbox (§13) — weight 13

> Sketch only; expand with writing-plans when Epic A lands. Needed for the large-payload `download_url` path.

### Stage 9 — Sandbox runtime (weight 8, L) — branch `stage-9-sandbox-runtime`
- [ ] Dockerfile (`python:3.12-slim`, non-root `sandbox` user, pinned data-science libs: pandas/numpy/matplotlib).
- [ ] Code-exec service (small HTTP service): accepts code, runs it in an ephemeral container with CPU/mem/time/PID caps, captures stdout/stderr/result + any produced artifact.
- [ ] Isolated `sandbox_net` Docker network + egress allowlist (iptables): MCP fileserver(s) + internet; **drop** Qdrant/app-DB/app-host.

### Stage 10 — `execute_code` tool binding + tests (weight 5, M) — branch `stage-10-execute-code`
- [ ] `app/tools/builtin/execute_code.py`: registry tool that POSTs to the code-exec service; returns result/error; large outputs follow the `download_url` convention.
- [ ] End-to-end: model calls `satudata-garut.get_dataset_data` → `download_url` → `execute_code` (`pd.read_csv(url); df.describe()`) → answer.
- [ ] Wall tests: sandbox cannot reach Qdrant/app-DB/app-host; caps enforced; container ephemeral.

---

## Epic C — Deployment — weight 5

### Stage 11 — Single-origin deploy (weight 5, M) — branch `stage-11-deploy`
- [ ] Reverse proxy (nginx/Caddy): SPA + `/api` same origin (auth cookie `SameSite=Lax`, `Secure=true` in prod).
- [ ] `docker-compose` for the full stack: api, qdrant, sandbox (+ net), satudata-mcp, fileserver, frontend build.
- [ ] Prod config: `COOKIE_SECURE=true`, CORS off (same-origin), GPU vs CPU split (embeddings/Surya on GPU host; Qdrant + sandbox on CPU VM, §13).
- [ ] Acceptance pass against spec §15 checklist.
