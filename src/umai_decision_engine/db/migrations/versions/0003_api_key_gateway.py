"""api_keys.gateway_id: a key bound to one gateway (credentials 0.5, ADR-0038)

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no default: existing keys stay unbound (gateway-api 0.9 selection rules).
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("gateway_id", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("gateway_id")
