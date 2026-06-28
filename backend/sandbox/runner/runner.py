"""In-container HTTP runner — persistent IPython shell per process.

The module-level ``_shell`` holds one ``InteractiveShell`` so globals (and
loaded dataframes) survive across POST /run calls within a session.

``run_cell`` is intentionally importable for unit tests — no HTTP server needed.
Matplotlib is imported lazily so the module loads in test environments that
only have IPython/FastAPI installed.
"""
from __future__ import annotations

import base64
import io
import os
import threading
import traceback
from typing import Any

# MPLBACKEND=Agg is set in the Dockerfile ENV.  The setdefault here makes the
# module importable in test environments where that var is absent.
os.environ.setdefault("MPLBACKEND", "Agg")

from fastapi import FastAPI  # noqa: E402
from IPython.core.interactiveshell import InteractiveShell  # noqa: E402
from IPython.utils.capture import capture_output  # noqa: E402
from pydantic import BaseModel  # noqa: E402

RUN_TIMEOUT: int = int(os.environ.get("RUN_TIMEOUT", "30"))

app = FastAPI(title="rag-sandbox-runner")

# Module-global persistent shell.
_shell: InteractiveShell = InteractiveShell.instance()


# ---------------------------------------------------------------------------
# Core logic — importable for unit tests (no HTTP required)
# ---------------------------------------------------------------------------


def _collect_matplotlib_artifacts() -> list[dict[str, str]]:
    """Return base64-PNG artifacts for any open matplotlib figures.

    Imported lazily so the module loads in envs where matplotlib is absent.
    """
    try:
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError:
        return []

    artifacts: list[dict[str, str]] = []
    for n in plt.get_fignums():
        fig = plt.figure(n)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        artifacts.append({"type": "image/png", "b64": base64.b64encode(buf.read()).decode()})
    plt.close("all")
    return artifacts


def run_cell(shell: InteractiveShell, code: str, timeout_s: int = 30) -> dict[str, Any]:
    """Execute *code* in *shell* with a wall-clock timeout.

    Returns ``{"stdout": str, "error": str | None, "artifacts": list[dict]}``.
    Globals in *shell* persist across successive calls.
    """
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

    result["artifacts"] = _collect_matplotlib_artifacts()
    return result


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


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
