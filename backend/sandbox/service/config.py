"""Sandbox service settings — all knobs in one place, driven from the environment.

Intentionally a plain class (not pydantic-settings) so:
  - ``RUN_TIMEOUT`` needs no ``SANDBOX_`` prefix (the runner reads it too).
  - Integration tests can mutate fields directly without env-var gymnastics.
"""
from __future__ import annotations

import os


class Settings:
    """Sandbox service settings."""

    def __init__(self) -> None:
        self.image: str = os.environ.get("SANDBOX_IMAGE", "rag-sandbox:latest")
        self.net: str = os.environ.get("SANDBOX_NET", "sandbox_net")
        self.mem: str = os.environ.get("SANDBOX_MEM", "2g")
        self.cpus: float = float(os.environ.get("SANDBOX_CPUS", "1.0"))
        self.pids: int = int(os.environ.get("SANDBOX_PIDS", "128"))
        self.idle_ttl: int = int(os.environ.get("SANDBOX_IDLE_TTL", "900"))
        self.run_timeout: int = int(os.environ.get("RUN_TIMEOUT", "30"))


settings = Settings()
