# Stage 9 — Code-Exec Sandbox Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone code-execution sandbox subsystem (runner image + persistent-IPython service + docker-py lifecycle) with four isolation walls, proven by a real Docker integration test.

**Architecture:** A sandbox container image (python:3.12-slim + IPython FastAPI runner) is managed per-conversation by a code-exec service (docker-py) running on an isolated `sandbox_net` bridge network. The runner holds one persistent `IPython.InteractiveShell` so globals survive across calls. The service API exposes `POST /sessions/{id}/execute`, `DELETE /sessions/{id}`, and `GET /health`.

**Tech Stack:** Python 3.12, FastAPI, IPython, docker-py (>=7), httpx, matplotlib, pytest, ruff.

## Global Constraints

- `from __future__ import annotations` in every Python file.
- Type hints throughout; match repo style (pydantic-settings for config).
- YAGNI — no Stage 10 wiring (no tool registry, no app integration).
- Do NOT edit `docs/BACKEND_SPEC.md` (LOCKED).
- Runner deps in `runner/requirements.txt` ONLY — never in `pyproject.toml`.
- `sandbox` extra in `pyproject.toml` adds only `docker>=7`.
- Run commands from `backend/` as: `env -u VIRTUAL_ENV uv run ...`.
- Tests in `backend/sandbox/tests/` (separate from `backend/tests/`).

---

### Task 9.1: pyproject.toml sandbox extra + uv sync

**Files:**
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `docker` package available in the venv for subsequent tasks.

- [ ] **Step 1: Add sandbox extra to pyproject.toml**

```toml
# add under [project.optional-dependencies]:
sandbox = [
    "docker>=7",   # docker-py: ContainerManager, tests
]
```

- [ ] **Step 2: Sync the environment**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend && \
env -u VIRTUAL_ENV uv sync --extra dev --extra rag --extra ingest --extra llm --extra mcp --extra sandbox
```

Expected: resolves and installs without error.

- [ ] **Step 3: Verify docker is importable**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend && \
env -u VIRTUAL_ENV uv run python -c "import docker; print(docker.__version__)"
```

Expected: prints a version string like `7.x.x`.

---

### Task 9.2: Runner image + runner.py + test_runner.py

**Files:**
- Create: `backend/sandbox/runner/Dockerfile`
- Create: `backend/sandbox/runner/requirements.txt`
- Create: `backend/sandbox/runner/runner.py`
- Create: `backend/sandbox/tests/__init__.py`
- Create: `backend/sandbox/tests/test_runner.py`

**Interfaces:**
- Produces:
  - `run_cell(shell, code, timeout_s) -> dict` — importable function for tests.
  - Docker image `rag-sandbox:latest` built from `backend/sandbox/runner/`.
  - `POST /run {code: str}` -> `{stdout: str, error: str|null, artifacts: list}`.

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.115
uvicorn[standard]>=0.30
ipython>=8
pandas>=2
numpy>=1
matplotlib>=3.8
httpx>=0.27
```

- [ ] **Step 2: Create runner.py**

```python
"""In-container HTTP runner — persistent IPython shell per process.

Importable for tests: use run_cell(shell, code, timeout_s) directly.
"""
from __future__ import annotations

import base64
import io
import os
import sys
import threading
import traceback
from typing import Any

from fastapi import FastAPI
from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import capture_output

import matplotlib
matplotlib.use("Agg")  # non-interactive; also set via MPLBACKEND env
import matplotlib.pyplot as plt

RUN_TIMEOUT: int = int(os.environ.get("RUN_TIMEOUT", "30"))

app = FastAPI(title="rag-sandbox-runner")

# Module-global persistent shell — globals/df survive across POST /run calls.
_shell: InteractiveShell = InteractiveShell.instance()


# ---------------------------------------------------------------------------
# Core logic (importable for unit tests — no HTTP needed)
# ---------------------------------------------------------------------------

def run_cell(shell: InteractiveShell, code: str, timeout_s: int = 30) -> dict[str, Any]:
    """Execute *code* in *shell* with a wall-clock timeout.

    Returns {"stdout": str, "error": str | None, "artifacts": list[dict]}.
    """
    result: dict[str, Any] = {"stdout": "", "error": None, "artifacts": []}
    exc_container: list[Exception] = []

    def _run() -> None:
        with capture_output() as cap:
            cell_result = shell.run_cell(code)
        result["stdout"] = cap.stdout or ""
        if cap.stderr and not result["stdout"]:
            result["stdout"] = cap.stderr
        if cell_result.error_in_exec is not None:
            tb = "".join(
                traceback.format_exception(
                    type(cell_result.error_in_exec),
                    cell_result.error_in_exec,
                    cell_result.error_in_exec.__traceback__,
                )
            )
            result["error"] = tb
        elif cell_result.error_before_exec is not None:
            result["error"] = str(cell_result.error_before_exec)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        result["error"] = f"Execution timed out after {timeout_s}s"
        return result

    # Collect matplotlib figures → base64 PNG artifacts
    fig_nums = plt.get_fignums()
    for n in fig_nums:
        fig = plt.figure(n)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        result["artifacts"].append({"type": "image/png", "b64": b64})
    plt.close("all")

    return result


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

class RunRequest(pydantic_model()):
    code: str


# Use pydantic via FastAPI's built-in support
from pydantic import BaseModel  # noqa: E402


class RunRequest(BaseModel):
    code: str


class Artifact(BaseModel):
    type: str
    b64: str


class RunResponse(BaseModel):
    stdout: str
    error: str | None = None
    artifacts: list[Artifact] = []


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    out = run_cell(_shell, req.code, timeout_s=RUN_TIMEOUT)
    return RunResponse(**out)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

Wait - I made a mistake with `pydantic_model()`. Let me fix that in the actual implementation file. The correct runner.py is:

```python
"""In-container HTTP runner — persistent IPython shell per process.

Importable for tests: use run_cell(shell, code, timeout_s) directly.
"""
from __future__ import annotations

import base64
import io
import os
import threading
import traceback
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fastapi import FastAPI
from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import capture_output
from pydantic import BaseModel

RUN_TIMEOUT: int = int(os.environ.get("RUN_TIMEOUT", "30"))

app = FastAPI(title="rag-sandbox-runner")

_shell: InteractiveShell = InteractiveShell.instance()


def run_cell(shell: InteractiveShell, code: str, timeout_s: int = 30) -> dict[str, Any]:
    result: dict[str, Any] = {"stdout": "", "error": None, "artifacts": []}

    def _run() -> None:
        with capture_output() as cap:
            cell_result = shell.run_cell(code)
        result["stdout"] = cap.stdout or ""
        if cell_result.error_in_exec is not None:
            tb = "".join(
                traceback.format_exception(
                    type(cell_result.error_in_exec),
                    cell_result.error_in_exec,
                    cell_result.error_in_exec.__traceback__,
                )
            )
            result["error"] = tb
        elif cell_result.error_before_exec is not None:
            result["error"] = str(cell_result.error_before_exec)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        result["error"] = f"Execution timed out after {timeout_s}s"
        return result

    for n in plt.get_fignums():
        fig = plt.figure(n)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        result["artifacts"].append({"type": "image/png", "b64": base64.b64encode(buf.read()).decode()})
    plt.close("all")

    return result


class RunRequest(BaseModel):
    code: str


class RunResponse(BaseModel):
    stdout: str
    error: str | None = None
    artifacts: list[dict] = []


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    return RunResponse(**run_cell(_shell, req.code, timeout_s=RUN_TIMEOUT))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 3: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

ENV MPLBACKEND=Agg

RUN useradd --uid 1000 --create-home sandbox

WORKDIR /work

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runner.py .

USER sandbox

CMD ["uvicorn", "runner:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Write test_runner.py**

```python
"""Unit tests for runner logic — no Docker, no HTTP."""
from __future__ import annotations

import sys
import time

import pytest
from IPython.core.interactiveshell import InteractiveShell

# Patch path so runner.py is importable without being installed
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "runner"))
from runner import run_cell  # noqa: E402


@pytest.fixture()
def shell() -> InteractiveShell:
    """Fresh shell per test."""
    InteractiveShell.clear_instance()
    sh = InteractiveShell.instance()
    yield sh
    InteractiveShell.clear_instance()


def test_stdout_captured(shell):
    out = run_cell(shell, "print(1 + 1)")
    assert out["error"] is None
    assert "2" in out["stdout"]


def test_persistence_across_calls(shell):
    run_cell(shell, "x = 42")
    out = run_cell(shell, "print(x)")
    assert out["error"] is None
    assert "42" in out["stdout"]


def test_exception_returns_traceback(shell):
    out = run_cell(shell, "raise ValueError('boom')")
    assert out["error"] is not None
    assert "ValueError" in out["error"]
    assert "boom" in out["error"]


def test_matplotlib_produces_artifact(shell):
    out = run_cell(shell, "import matplotlib.pyplot as plt; plt.plot([1,2,3]); plt.show()")
    assert out["error"] is None
    assert len(out["artifacts"]) >= 1
    art = out["artifacts"][0]
    assert art["type"] == "image/png"
    assert len(art["b64"]) > 100  # non-trivial base64


def test_timeout_returns_error(shell):
    start = time.monotonic()
    out = run_cell(shell, "while True: pass", timeout_s=2)
    elapsed = time.monotonic() - start
    assert out["error"] is not None
    assert "timed out" in out["error"].lower()
    assert elapsed < 10  # well under 10s
```

- [ ] **Step 5: Run tests**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend && \
env -u VIRTUAL_ENV uv run pytest sandbox/tests/test_runner.py -v
```

Expected: 5 passed.

---

### Task 9.3: service/config.py + service/containers.py + test_service.py (partial)

**Files:**
- Create: `backend/sandbox/service/__init__.py`
- Create: `backend/sandbox/service/config.py`
- Create: `backend/sandbox/service/containers.py`
- Create: `backend/sandbox/tests/test_service.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings): `SANDBOX_IMAGE`, `SANDBOX_NET`, `SANDBOX_MEM`, `SANDBOX_CPUS`, `SANDBOX_PIDS`, `SANDBOX_IDLE_TTL`, `RUN_TIMEOUT`.
  - `ContainerManager`: `ensure_network()`, `get_or_create(session_id)`, `execute(session_id, code)`, `remove(session_id)`, `reap_idle()`.

- [ ] **Step 1: Write config.py**

```python
from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SANDBOX_", extra="ignore")

    image: str = "rag-sandbox:latest"
    net: str = "sandbox_net"
    mem: str = "2g"
    cpus: float = 1.0
    pids: int = 128
    idle_ttl: int = 900  # seconds

    # No SANDBOX_ prefix for RUN_TIMEOUT — runner reads it too
    model_config = SettingsConfigDict(extra="ignore")

    run_timeout: int = 30


settings = Settings()
```

Actually there's a conflict — `SANDBOX_` prefix from `env_prefix` would make `RUN_TIMEOUT` become `SANDBOX_RUN_TIMEOUT`. The brief says `RUN_TIMEOUT` (no prefix). Use a clean config:

```python
from __future__ import annotations

import os


class Settings:
    image: str
    net: str
    mem: str
    cpus: float
    pids: int
    idle_ttl: int
    run_timeout: int

    def __init__(self) -> None:
        self.image = os.environ.get("SANDBOX_IMAGE", "rag-sandbox:latest")
        self.net = os.environ.get("SANDBOX_NET", "sandbox_net")
        self.mem = os.environ.get("SANDBOX_MEM", "2g")
        self.cpus = float(os.environ.get("SANDBOX_CPUS", "1.0"))
        self.pids = int(os.environ.get("SANDBOX_PIDS", "128"))
        self.idle_ttl = int(os.environ.get("SANDBOX_IDLE_TTL", "900"))
        self.run_timeout = int(os.environ.get("RUN_TIMEOUT", "30"))


settings = Settings()
```

- [ ] **Step 2: Write containers.py** (see Task description above for contract)

- [ ] **Step 3: Write test_service.py** with mocked docker

- [ ] **Step 4: Run tests**

---

### Task 9.4: service/main.py + route tests in test_service.py

**Files:**
- Create: `backend/sandbox/service/main.py`

---

### Task 9.5: test_integration.py

**Files:**
- Create: `backend/sandbox/tests/test_integration.py`

**Key tests:** stdout, persistence, non-root, filesystem wall, network wall (critical), teardown.

---

### Task 9.6: README.md + final cleanup

**Files:**
- Create: `backend/sandbox/README.md`

---

## Execution

This plan is executed inline in the current session. See implementation for all files.
