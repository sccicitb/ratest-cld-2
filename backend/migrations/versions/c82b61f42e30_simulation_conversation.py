"""Persist session simulation snapshots and active clarification.

Revision ID: c82b61f42e30
Revises: aa4dbcb609fc
"""

import sqlalchemy as sa
from alembic import op

revision = "c82b61f42e30"
down_revision = "aa4dbcb609fc"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sessions", sa.Column("simulation_state", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("sessions", "simulation_state")
