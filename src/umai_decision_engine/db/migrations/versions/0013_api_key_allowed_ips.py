"""api_keys.allowed_ips: the networks a key may be used from (credentials 0.9, ADR-0046)

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL for every existing key: no restriction, as before.
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("allowed_ips", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("allowed_ips")
