"""Sandbox test configuration — marker registration."""
from __future__ import annotations


def pytest_configure(config: object) -> None:
    config.addinivalue_line(  # type: ignore[attr-defined]
        "markers",
        "docker: mark test as requiring a running Docker daemon (auto-skipped if unavailable)",
    )
