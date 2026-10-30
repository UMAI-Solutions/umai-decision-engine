"""usage_events.provider and .model: the gateway's meters name where the call went
(contract usage 0.2, ADR-0041)

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("usage_events") as batch:
        batch.add_column(sa.Column("provider", sa.String(64), nullable=True))
        batch.add_column(sa.Column("model", sa.String(128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usage_events") as batch:
        batch.drop_column("model")
        batch.drop_column("provider")
