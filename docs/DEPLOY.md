# Production Deployment Runbook

Topology: **Windows Server (GPU host)** runs the FastAPI backend natively.
**Hyper-V Linux VM** runs Docker services (Qdrant + code-exec sandbox).
**Cloudflare Tunnel** (`cloudflared`, already running on the Windows host) provides
public HTTPS access at your domain → `http://localhost:8000`.
No nginx/Caddy reverse proxy.  FastAPI serves both the SPA and `/api`.

---

## 1 · Build the Frontend

On the Windows host (or any machine with Node 20+):

```bash
cd frontend
npm install
npm run build          # outputs to frontend/build/client/
```

The backend's `SPA_DIR=../frontend/build/client` (in `.env`) tells FastAPI where to
find `index.html` + `assets/`.

---

## 2 · Prepare the Linux VM

### 2a · Hyper-V networking

Ensure the VM has a Hyper-V virtual switch that makes the VM reachable from the
Windows host.  A **Default Switch** (NAT) works; note the VM's IP address
(e.g. `ip addr`).  You'll use it for `QDRANT_URL` and `CODE_EXEC_URL` in `.env`.

### 2b · Build the runner image (one-time, or on dependency changes)

```bash
# From the repo root on the VM (or wherever you cloned the repo):
docker build -t rag-sandbox backend/sandbox/runner
```

The runner image is NOT in the docker-compose; it must be built manually because
the compose only manages the *service* (code-exec), not the ephemeral runner containers.

### 2c · Start Docker services

```bash
docker compose -f deploy/docker-compose.prod.yml up -d
```

This starts:
- **qdrant** — vector store, persistent volume `qdrant_data`, ports 6333/6334 bound to `0.0.0.0`
- **code-exec** — sandbox controller, port 8001, docker.sock mounted, image built from `backend/sandbox/code-exec.Dockerfile`

Verify:
```bash
docker compose -f deploy/docker-compose.prod.yml ps
curl http://localhost:6333/healthz          # Qdrant
curl http://localhost:8001/health           # code-exec service
```

### 2d · Apply sandbox egress allowlist (iptables)

After Docker starts (so `sandbox_net` exists):

```bash
FILESERVER_IP=<MCP_FILESERVER_IP> \
FILESERVER_PORT=8080 \
QDRANT_IP=127.0.0.1 \
APP_HOST_IP=<WINDOWS_HOST_IP> \
bash deploy/sandbox-iptables.sh
```

This blocks sandbox containers from reaching Qdrant, the app DB, the Windows host,
and all RFC-1918 ranges, while allowing MCP fileserver(s) and public internet.

To persist across reboots (Ubuntu/Debian):
```bash
apt install iptables-persistent
iptables-save > /etc/iptables/rules.v4
```

---

## 3 · Configure and Run the Backend (Windows Host)

### 3a · Install dependencies

```powershell
# In the backend/ directory:
uv sync --all-extras
```

### 3b · Create the .env file

```powershell
copy backend\.env.prod.example backend\.env
```

Edit `backend/.env` and fill in all `<placeholder>` values:
- `QDRANT_URL=http://<VM_IP>:6333`
- `CODE_EXEC_URL=http://<VM_IP>:8001`
- `MODEL_BASE_URL` — your llama-server endpoint
- `JWT_SECRET` — a 32+ byte random string (use `python -c "import secrets; print(secrets.token_hex(32))"`)
- `COOKIE_SECURE=true`
- `SPA_DIR=../frontend/build/client`

### 3c · OCR models (PDFOxide / PaddleOCR)

Scanned-PDF OCR needs onnxruntime (installed as a dependency) and ~21 MB of
ONNX models. Provision once per deploy:

```powershell
cd backend
uv run python scripts/setup_ocr_models.py
```

Air-gapped hosts (no outbound internet): run the above with `--manifest` on a
connected machine, fetch the listed files, and drop them into the directory
named by `PDF_OXIDE_MODEL_DIR` on the target. The app sets `ORT_DYLIB_PATH`
itself at startup; if onnxruntime is missing, OCR degrades to native text (no crash).

### 3d · Run the database migration

```powershell
cd backend
uv run alembic upgrade head
```

### 3e · Start the backend

```powershell
cd backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The backend serves both `/api/*` and the built SPA at `/`.

### 3f · Run as a Windows service (NSSM)

For production, use [NSSM](https://nssm.cc/) to manage the process:

```powershell
nssm install rag-backend "C:\path\to\uv.exe"
nssm set rag-backend AppParameters "run uvicorn app.main:app --host 0.0.0.0 --port 8000"
nssm set rag-backend AppDirectory "C:\path\to\repo\backend"
nssm set rag-backend AppEnvironmentExtra "VIRTUAL_ENV="
nssm start rag-backend
```

---

## 4 · Cloudflare Tunnel Ingress

The tunnel is already running on the Windows host.  Add the ingress rule to
`~/.cloudflared/config.yml` (or `%USERPROFILE%\.cloudflared\config.yml`):

```yaml
tunnel: <YOUR_TUNNEL_ID>
credentials-file: C:\Users\<USER>\.cloudflared\<TUNNEL_ID>.json

ingress:
  - hostname: your-domain.example.com
    service: http://localhost:8000
  # catch-all rule (required by cloudflared):
  - service: http_status:404
```

Then restart cloudflared:
```powershell
cloudflared service restart
```

FastAPI handles HTTPS implicitly — Cloudflare terminates TLS and forwards plain
HTTP to `localhost:8000`.  The `COOKIE_SECURE=true` setting works because the
browser sees HTTPS (Cloudflare's side).

---

## 5 · Verify

After everything is up:

1. **Open the domain** in a browser → the SPA loads.
2. **Sign in** → POST `/api/auth/login` returns a JWT, refresh cookie is set.
3. **KB search** → upload a document via the UI, ask a question → retrieval returns chunks.
4. **Execute code** → ask the assistant to run a Python snippet → SSE events include `calling_tool` + `tool_result`.

Smoke-test the API directly:
```bash
curl https://your-domain.example.com/api/health
# {"status":"ok"}
```

---

## 6 · §15 Acceptance Checklist

| Checklist item | How to verify in prod |
|---|---|
| Auth: login issues JWT; `me` validates; bad token → 401 | Sign in, call `/api/auth/me`, send bad Bearer → 401 |
| Sessions CRUD; list sorted `updatedAt desc`; delete cascades | Create/list/delete sessions via UI or API |
| Messages chronological; attachments include inline + ingested | Upload file, send message, GET messages |
| Chat is agentic: `calling_tool` SSE per search call | Ask a question requiring retrieval; watch SSE stream |
| Scope is server-injected: no cross-session leak | Two users; verify their chunks don't cross |
| Ingress is token-based: small → inline, large → ingest | Send small (<6k tokens) and large files |
| Scanned PDFs → PDFOxide OCR → chunks/embeddings | Upload a scanned PDF; verify chunks in KB |
| Attachment bytes upload at send: SSE streams chunk_progress + attachment_resolved | Upload via chat UI; inspect SSE |
| KB upload: multipart SSE; polling while `indexing` | Upload to KB; watch `indexing → ready` transition |
| Auth cookie: httpOnly/Secure/SameSite=Lax; refresh-on-load + 401→refresh→retry | Check browser DevTools → Cookies; network tab |
| Tool loop is model-agnostic; no vendor SDK | Backend code only uses OpenAI-compatible endpoint |
| Loop bounded: MAX_TOOL_ITERATIONS / MAX_PARALLEL_TOOLS | Set MAX_TOOL_ITERATIONS=1; complex query falls back to text |
| Retrieval is hybrid: dense+sparse (BGE-M3) + RRF | Check logs for dual embedding; verify BGE-M3 model loaded |
| DB is portable: SQLite/Postgres via connection string | Change DATABASE_URL to Postgres; run alembic upgrade head |
| Two stores consistent: Qdrant + SQLite; delete removes points | Delete a KB file; verify Qdrant points removed |
| Sandbox walled: non-root, mem/CPU capped, egress allowlist | Run `execute_code`; verify sandbox containers; iptables rules |
| Session files: scope=session; not in KB; promote flips scope | Upload via chat; verify absent from KB; promote; verify present |
| KB upload validates; reindex/tags/delete per §8.3 | Test each operation via UI |
| Errors use `{ message, code }` with correct status codes | Trigger 404, 422, 401 — check JSON shape |
| Frontend uses real fetch; UI works end-to-end | Full UI walkthrough |

**Not applicable in this deployment:** items that require a CI/CD pipeline or staging environment are verified manually per this runbook.
