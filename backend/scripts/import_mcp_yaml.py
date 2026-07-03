"""One-shot migration: import mcp.yaml entries into the M4a DB catalog.

Usage (from the backend directory):
    env -u VIRTUAL_ENV uv run python scripts/import_mcp_yaml.py [path/to/mcp.yaml]

Default path is the value of MCP_CONFIG_PATH setting (./mcp.yaml).

For each server in the YAML:
  - auth.type=none     → inserted directly (no encryption).
  - auth.type=bearer   → reads the env var named by auth.token_env and
                          Fernet-encrypts it (requires MCP_TOKEN_KEY to be set).

Idempotent: skips servers whose name already exists in the catalog.
Set enabled=True in the YAML to have the server start enabled; default is
False in the DB (admin must explicitly enable after import if YAML omits it).
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Bootstrap: resolve project root so bare `uv run python scripts/...` works
# ---------------------------------------------------------------------------
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import logging  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.mcp.config import load_mcp_config  # noqa: E402
from app.models import MCPServer  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def _encrypt(plain: str) -> str:
    from app.mcp.crypto import encrypt_token

    return encrypt_token(plain)


def main(yaml_path: str | None = None) -> None:
    cfg = load_mcp_config(yaml_path)
    if not cfg.mcp_servers:
        log.info("No servers in config — nothing to import.")
        return

    db = SessionLocal()
    try:
        imported = 0
        skipped = 0
        for srv in cfg.mcp_servers:
            existing = db.query(MCPServer).filter(MCPServer.name == srv.name).first()
            if existing:
                log.info("  SKIP  %r — already in catalog (id=%s).", srv.name, existing.id)
                skipped += 1
                continue

            token_encrypted: str | None = None
            auth_type = srv.auth.type
            if auth_type == "bearer":
                import os

                env_var = srv.auth.token_env
                if not env_var:
                    log.error("  ERROR %r — bearer auth missing token_env; skipped.", srv.name)
                    skipped += 1
                    continue
                raw = os.environ.get(env_var)
                if not raw:
                    log.error(
                        "  ERROR %r — env var %r is not set; skipped.", srv.name, env_var
                    )
                    skipped += 1
                    continue
                token_encrypted = _encrypt(raw)

            row = MCPServer(
                name=srv.name,
                transport=srv.transport,
                url=srv.url,
                auth_type=auth_type,
                token_encrypted=token_encrypted,
                enabled=srv.enabled,
            )
            db.add(row)
            db.commit()
            log.info(
                "  ADD   %r  url=%s  auth=%s  enabled=%s.",
                srv.name,
                srv.url,
                auth_type,
                srv.enabled,
            )
            imported += 1

        log.info("Done: %d imported, %d skipped.", imported, skipped)
    finally:
        db.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    main(path)
