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
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — the first admin, created on boot (§3e).
  Required on a fresh database: there is no signup route.
- `MCP_TOKEN_KEY` — Fernet key encrypting MCP bearer tokens at rest (`python -c
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
  Needed before adding a server token in `/admin`; keep it with the database.
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

### 3e · First admin

A migrated database is **empty**, and accounts are admin-provisioned — there is
no signup route.  The only way in is the startup bootstrap: set `ADMIN_EMAIL`
and `ADMIN_PASSWORD` in `backend/.env` (§3b) and the app creates that admin on
boot, or promotes the user if the email already exists.  It is idempotent, so
leave the values set across restarts.

Everything else — users, groups, KB group assignments, the MCP catalog — is
created from `/admin` once you are logged in.

> **Do not run `python -m app.seed` in production.**  That is the dev seed: it
> creates `demo@example.com` with the well-known password `demo1234`, which on
> a Cloudflare-exposed origin is an open door.  It exists for local development
> parity with the frontend's login pre-fill.

### 3f · Start the backend

```powershell
cd backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The backend serves both `/api/*` and the built SPA at `/`.

### 3g · Run as a Windows service (NSSM)

For production, use [NSSM](https://nssm.cc/) to manage the process:

```powershell
nssm install rag-backend "C:\path\to\uv.exe"
nssm set rag-backend AppParameters "run uvicorn app.main:app --host 0.0.0.0 --port 8000"
nssm set rag-backend AppDirectory "C:\path\to\repo\backend"
nssm set rag-backend AppEnvironmentExtra "VIRTUAL_ENV="
nssm start rag-backend
```

### 3h · Voice service (STT)

Voice input is optional and lives entirely outside the main backend process: a
separate FastAPI sidecar (`backend/voice/`) that transcribes audio and is
called over HTTP. Leave `VOICE_SERVICE_URL` unset in `backend/.env` (§3b) and
the app runs exactly as before, minus the mic button — nothing else changes.

#### Prerequisite: CUDA 12 + cuDNN 9 (do this first)

**The sidecar will not start on the GPU without these.** faster-whisper runs on
CTranslate2, and our lockfile carries no `nvidia-*` wheels — CTranslate2 loads
cuBLAS and cuDNN 9 from the *system*, not from the venv. With `STT_DEVICE`
unset it defaults to `auto`, which selects CUDA the moment a device is visible,
and `WhisperModel(...)` then fails loading `cudnn_*.dll`. That exception comes
out of the sidecar's startup, so uvicorn exits and NSSM restarts it in a loop.

On the Windows host, install:

- **CUDA Toolkit 12.x** (`nvcc --version` should report 12.x)
- **cuDNN 9 for CUDA 12** — copy its `bin\*.dll` next to the CUDA runtime, or
  add its `bin` to the system `PATH`

Then confirm the DLLs are actually resolvable by the service account NSSM runs
as, not just by your interactive shell:

```powershell
nvidia-smi
where cudnn64_9.dll cublas64_12.dll
```

If you need voice working before the CUDA side is sorted, set `STT_DEVICE=cpu`
in the sidecar's environment. It starts and transcribes correctly — just far
slower (int8 on CPU), which is a usable stopgap and a clean way to prove the
rest of the wiring before blaming the GPU.

Install the sidecar's own dependencies (it has its own `pyproject.toml`,
separate from the backend's):

```powershell
cd backend\voice
uv sync
```

Prefetch the faster-whisper model once per deploy, so the GPU host never
hits HuggingFace at runtime — on an air-gapped host that means a hang and a
failed transcription, not a slow download:

```powershell
cd backend\voice
uv run python ..\scripts\setup_stt_model.py
```

Air-gapped hosts (no outbound internet at all): run the same command with
`--manifest` on a connected machine, fetch the five listed files, drop them into
one directory on the target, and set `STT_MODEL_DIR` to that directory in the
voice service's environment. They are already a converted CTranslate2 model
directory — these repos are published pre-converted, so there is no conversion
step on either machine.

Take the URLs from `--manifest` rather than constructing them: the repo is *not*
a predictable `Systran/faster-whisper-<model>`. The default `large-v3-turbo`
lives at `mobiuslabsgmbh/faster-whisper-large-v3-turbo` while `large-v3` is at
`Systran/faster-whisper-large-v3`, and the script reads the same mapping the
runtime uses, so the two cannot drift.

`STT_MODEL_DIR` takes precedence over `STT_MODEL` when set, and `/health`
reports whichever one actually loaded — the directory path if you used it,
the model name otherwise.

Start the sidecar:

```powershell
cd backend\voice
uv run uvicorn voice.service.main:app --host 0.0.0.0 --port 8002
```

Register it as a second NSSM service, `rag-voice`, alongside `rag-backend`:

```powershell
nssm install rag-voice "C:\path\to\uv.exe"
nssm set rag-voice AppParameters "run uvicorn voice.service.main:app --host 0.0.0.0 --port 8002"
nssm set rag-voice AppDirectory "C:\path\to\repo\backend\voice"
nssm set rag-voice AppEnvironmentExtra "VIRTUAL_ENV="
nssm start rag-voice
```

Verify with:

```powershell
curl http://localhost:8002/health
```

`/health` reports the engine, model, and device that **actually loaded** — not
just what you set in the environment. It answers even while a transcription is
running, so it is a real liveness check, not a "is the GPU idle" check.

What it does and does not catch:

- **`STT_MODEL` typo** — you will not reach `/health` at all. An unrecognised
  name raises at startup and the service exits (NSSM will keep restarting it);
  nothing falls back to a default. Read the sidecar's log: the error names the
  bad value and lists the valid ones.
- **`STT_DEVICE`** — this is what `/health` is for. `auto` silently choosing
  `cpu` because CUDA wasn't visible is a *working* service that is ~20x too
  slow, and the `device` field is the only place that shows it.
- **`STT_MODEL_DIR` vs `STT_MODEL`** — the `model` field tells you which one
  won.

Once it looks right, set `VOICE_SERVICE_URL=http://localhost:8002` in
`backend/.env` and restart `rag-backend` to enable the mic button. The backend
probes this `/health` once at startup and only reports `stt: true` to the
frontend if it answered — so **order matters**: start `rag-voice` first, then
restart `rag-backend`. If you set the URL while the sidecar is down, the mic
stays hidden (by design, not a bug) until the next `rag-backend` restart.

Two limits apply to every recording. `MAX_AUDIO_BYTES` (backend, 10 MiB) and
`STT_MAX_AUDIO_SECONDS` (sidecar, 120) — the second is the one that matters,
since Opus only reaches 10 MiB at around 40 minutes. The browser shows an
elapsed timer and stops recording at 2:00 on its own; the sidecar rejects
anything longer with `audio_too_long`.

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

---

## 7 · Reset — start a deployment fresh

Wipes all application state.  Stop the backend (or `nssm stop rag-backend`) first.

Persistent state lives in exactly three places:

**1 · SQLite + blobs (Windows host)** — users, groups, sessions, messages, KB
records, MCP catalog, artifacts, plus the uploaded file bytes.

```powershell
cd backend
Remove-Item -Recurse -Force data    # app.db, app.db-wal, app.db-shm, blobs/
```

Both directories are recreated on demand, so deleting `data/` wholesale is safe.

**2 · Qdrant collection (Linux VM)** — the vectors.  Recreated automatically on
the first ingest:

```bash
curl -X DELETE http://<VM_IP>:6333/collections/kb_chunks
```

**3 · Nothing else.**  OCR models (§3c) are a cache, not state — no need to
re-provision unless you wiped them too.

Then bring it back up:

```powershell
cd backend
uv run alembic upgrade head        # recreate the schema
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # bootstraps the admin (§3e)
```

> **`MCP_TOKEN_KEY` and the database travel together.**  Stored MCP bearer
> tokens are Fernet-encrypted with that key; rotating it orphans them.  A full
> wipe is the one safe moment to change it.
