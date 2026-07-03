"""One-shot Qdrant back-fill (M3): mark existing kb chunks as public.

Existing `scope=kb` points pre-date the group gate and lack `is_public`/`group_id`
in their payload. The M3 retrieval filter requires `is_public OR group_id ∈ groups`
for the kb branch, so without this back-fill existing KB chunks become invisible.

Run ONCE on deploy, AFTER `alembic upgrade head` (the DB rows are back-filled by
the migration; this patches the denormalized Qdrant payload to match):

    PYTHONPATH=. uv run python scripts/backfill_kb_public_payload.py

Idempotent — safe to re-run. New ingests already carry the fields.
"""
from __future__ import annotations

from qdrant_client import models as qm

from app.rag.vectors import COLLECTION, get_client


def main() -> None:
    client = get_client()
    # Set is_public=True (+ group_id=None) on every point with scope == "kb".
    client.set_payload(
        collection_name=COLLECTION,
        payload={"is_public": True, "group_id": None},
        points=qm.Filter(
            must=[qm.FieldCondition(key="scope", match=qm.MatchValue(value="kb"))]
        ),
    )
    print("Back-fill complete: existing kb chunks marked is_public=True.")


if __name__ == "__main__":
    main()
