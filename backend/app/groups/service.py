"""Group-related helpers used across M3/M4."""
from __future__ import annotations

from app.models import User


def group_ids_for(user: User) -> list[str]:
    """Return the list of group IDs the user belongs to.

    Accesses ``user.groups`` (lazy-loaded by SQLAlchemy if not yet eager-loaded).
    M3 uses this for the per-user access filter; M4 uses it for MCP resolution.
    """
    return [g.id for g in user.groups]
