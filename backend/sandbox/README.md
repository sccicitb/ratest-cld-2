# Sandbox — Code-Exec Service

Standalone code-execution subsystem for the RAG backend. **Stage 9 artifact.**
Wired into the app tool registry in Stage 10; this stage builds and tests it in isolation.

## Architecture

```
app (Stage 10)
    │  POST /sessions/{id}/execute
    ▼
code-exec service  (sandbox/service/main.py)
    │  docker-py: one container per conversation
    ▼
sandbox container  (sandbox/runner/)
    │  persistent IPython.InteractiveShell
    │  POST /run → {stdout, error, artifacts}
    ▼
  rag-sandbox:latest image
  (python:3.12-slim + ipython + pandas + matplotlib)
```

## Build the Runner Image

```bash
# From the repo root:
docker build -t rag-sandbox backend/sandbox/runner

# Or from backend/:
docker build -t rag-sandbox sandbox/runner
```

## Run the Code-Exec Service

```bash
# Prerequisites: rag-sandbox image built; Docker daemon running.
cd backend/
env -u VIRTUAL_ENV uv run uvicorn sandbox.service.main:app --port 8001
```

The service creates a `sandbox_net` bridge on startup.

## Run the Tests

```bash
cd backend/

# Unit tests only (no Docker required):
env -u VIRTUAL_ENV uv run pytest sandbox/tests/test_runner.py sandbox/tests/test_service.py -v

# All sandbox tests including real Docker integration:
env -u VIRTUAL_ENV uv run pytest sandbox/tests/ -v
```

The `@pytest.mark.docker` tests are auto-skipped if the Docker daemon is unreachable.

## Dev Network Model

```
Mac host (OrbStack)
├── sandbox_net (Docker bridge, internal=True in tests / regular in prod)
│   └── rag-sandbox containers (one per conversation)
│       └── runner: IPython + FastAPI on :8000
└── other services (Qdrant, app DB, etc.) — on their own networks
```

The service (host process) reaches each runner either via:
- **Published host port** (standard Linux Docker): `http://127.0.0.1:{random_port}/`
- **Direct container IP** (OrbStack): `http://{container_ip}:8000/` (OrbStack routes
  container IPs to the host even for `--internal` networks)

The `ContainerManager._wait_ready` tries both strategies automatically.

> ⚠️ **DEV DOES NOT ENFORCE THE NETWORK WALL.** On OrbStack and Docker Desktop
> for Mac, a *regular* Docker bridge does **not** add iptables DROP rules between
> bridge networks — so in dev, sandbox containers on `sandbox_net` **CAN reach**
> other Docker services on the host (Qdrant, the app DB, the app) and the host
> itself via `host.docker.internal`. This is the very thing the network wall is
> meant to prevent. The dev `sandbox_net` is a regular bridge (so the runner has
> the internet egress it needs to fetch `download_url`s); the integration tests
> use a separate `internal=True` network only to *prove the isolation mechanism*.
> **The real network wall is enforced solely by the PROD iptables rules below
> (Epic C), applied on the Linux VM. Do not treat dev as isolated, and do not
> deploy to prod without these rules.**

## Four Isolation Walls

| Wall | Mechanism | Kwarg | Enforced in dev? |
|------|-----------|-------|------------------|
| Filesystem | `read_only=True` + `/work` & `/tmp` as tmpfs | `read_only`, `tmpfs` | ✅ yes |
| Privilege | Non-root user; all capabilities dropped | `user="sandbox"`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]` | ✅ yes |
| Resources | Memory, CPU, and PID limits | `mem_limit`, `nano_cpus`, `pids_limit` | ✅ yes |
| Network | Sandbox on its own bridge; egress allowlist (internet/fileserver OK, internal services blocked) | `network=settings.net` **+ prod iptables** | ⚠️ **PROD ONLY** (see warning above) |

## PROD Iptables Egress Allowlist (Linux VM — Epic C)

In the prod Linux VM, the sandbox network is a **regular** bridge (NOT `--internal`).
The runner must reach the MCP fileserver(s) and the public internet, but must
never reach Qdrant, the app DB, or the app host.

Apply these iptables rules **on the Linux VM** after Docker starts:

```bash
# Identify the sandbox_net bridge interface (e.g. br-xxxxxxxx):
SANDBOX_BR=$(docker network inspect sandbox_net --format '{{.Options.com.docker.network.bridge.name}}')
# If empty, look for the bridge: ip link | grep br-
# then: SANDBOX_BR=br-<network_id_prefix>

# ---- Block internal services ----
# Drop traffic from sandbox to Qdrant (adjust IP/port to your deployment):
iptables -I FORWARD -i "$SANDBOX_BR" -d <QDRANT_HOST_IP> -p tcp --dport 6333 -j DROP
# Drop traffic to app DB (SQLite is local; only relevant if you move to Postgres):
iptables -I FORWARD -i "$SANDBOX_BR" -d <APP_DB_HOST_IP> -p tcp --dport 5432 -j DROP
# Drop traffic to the app server itself:
iptables -I FORWARD -i "$SANDBOX_BR" -d <APP_HOST_IP> -p tcp -j DROP
# Drop traffic to the VM's local loopback (host.docker.internal):
iptables -I FORWARD -i "$SANDBOX_BR" -d 127.0.0.0/8 -j DROP
# Drop all other RFC-1918 private addresses:
iptables -I FORWARD -i "$SANDBOX_BR" -d 10.0.0.0/8 -j DROP
iptables -I FORWARD -i "$SANDBOX_BR" -d 172.16.0.0/12 -j DROP
iptables -I FORWARD -i "$SANDBOX_BR" -d 192.168.0.0/16 -j DROP

# ---- Allow MCP fileserver(s) ----
# (insert BEFORE the DROP rules above; adjust IPs/ports)
iptables -I FORWARD -i "$SANDBOX_BR" -d <FILESERVER_IP> -p tcp --dport <FILESERVER_PORT> -j ACCEPT

# ---- Allow public internet ----
# By default, Docker's ACCEPT policy for FORWARD + the MASQUERADE rule on
# POSTROUTING already allows egress to public IPs.  The DROP rules above
# narrow this to block private ranges only.

# ---- Persist ----
# Ubuntu/Debian:  iptables-save > /etc/iptables/rules.v4
# RHEL/CentOS:    service iptables save
```

> **Docker Desktop residual (Mac dev only):** On Docker Desktop for Mac,
> `host.docker.internal` resolves to the host machine. The prod iptables rules
> above explicitly drop that range. This is a no-op on the Linux VM where
> `host.docker.internal` is not set up, but documents the intent.

## Idle Reaper

The service evicts containers idle for more than `SANDBOX_IDLE_TTL` seconds
(default 900 s / 15 min). Containers are also destroyed on session delete
(Stage 10 wires this to the session DELETE endpoint).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_IMAGE` | `rag-sandbox:latest` | Runner Docker image |
| `SANDBOX_NET` | `sandbox_net` | Isolated bridge network name |
| `SANDBOX_MEM` | `2g` | Container memory limit |
| `SANDBOX_CPUS` | `1.0` | Container CPU quota |
| `SANDBOX_PIDS` | `128` | Max processes in container |
| `SANDBOX_IDLE_TTL` | `900` | Idle eviction timeout (seconds) |
| `RUN_TIMEOUT` | `30` | Per-execution wall-clock timeout (seconds) |
