"""Keep completed simulation results with their assistant message.

Revision ID: e91a4f7c62b0
Revises: c82b61f42e30
"""

import sqlalchemy as sa
from alembic import op

revision = "e91a4f7c62b0"
down_revision = "c82b61f42e30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("simulation_results", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("messages", "simulation_results")
