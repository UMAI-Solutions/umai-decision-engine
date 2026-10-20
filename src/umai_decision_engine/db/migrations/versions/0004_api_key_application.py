"""api_keys.gateway_id → application_id: a key belongs to an application (credentials 0.6,
ADR-0039)

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A rename keeps existing bindings: the gateway of ADR-0038 is the application of ADR-0039.
    with op.batch_alter_table("api_keys") as batch:
        batch.alter_column(
            "gateway_id", new_column_name="application_id", existing_type=sa.String(64)
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.alter_column(
            "application_id", new_column_name="gateway_id", existing_type=sa.String(64)
        )
