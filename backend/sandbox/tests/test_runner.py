"""Unit tests for runner logic — in-process, no Docker, no HTTP.

Imports ``run_cell`` directly from runner/runner.py by adding the runner/
directory to sys.path (runner.py is not a package; it lives in the image).
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

# runner/ is not an installed package — add it to the path so we can import it.
_RUNNER_DIR = Path(__file__).parent.parent / "runner"
if str(_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNER_DIR))

from runner import run_cell  # noqa: E402

_MATPLOTLIB_AVAILABLE = importlib.util.find_spec("matplotlib") is not None


@pytest.fixture()
def shell():
    """Fresh IPython shell per test — prevents state leaking between tests."""
    from IPython.core.interactiveshell import InteractiveShell

    InteractiveShell.clear_instance()
    sh = InteractiveShell.instance()
    yield sh
    InteractiveShell.clear_instance()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stdout_captured(shell):
    out = run_cell(shell, "print(1 + 1)")
    assert out["error"] is None
    assert "2" in out["stdout"]


def test_persistence_across_calls(shell):
    """Globals survive across separate run_cell calls on the same shell."""
    run_cell(shell, "x = 42")
    out = run_cell(shell, "print(x)")
    assert out["error"] is None
    assert "42" in out["stdout"]


def test_exception_returns_traceback(shell):
    out = run_cell(shell, "raise ValueError('boom')")
    assert out["error"] is not None
    assert "ValueError" in out["error"]
    assert "boom" in out["error"]
    assert out["stdout"] is not None  # stdout may be empty; error is the key field


@pytest.mark.skipif(not _MATPLOTLIB_AVAILABLE, reason="matplotlib not installed in app venv")
def test_matplotlib_produces_artifact(shell):
    """Open matplotlib figures are captured as base64 PNG artifacts."""
    # Close any lingering figures first
    import matplotlib.pyplot as plt

    plt.close("all")

    out = run_cell(shell, "import matplotlib.pyplot as plt; plt.figure(); plt.plot([1, 2, 3])")
    assert out["error"] is None
    assert len(out["artifacts"]) >= 1
    art = out["artifacts"][0]
    assert art["type"] == "image/png"
    assert len(art["b64"]) > 100  # non-trivial PNG data


def test_timeout_returns_error(shell):
    start = time.monotonic()
    out = run_cell(shell, "while True: pass", timeout_s=2)
    elapsed = time.monotonic() - start
    assert out["error"] is not None
    assert "timed out" in out["error"].lower()
    assert elapsed < 8  # must return well within the test budget
