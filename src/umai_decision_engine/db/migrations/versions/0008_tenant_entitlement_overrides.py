"""tenants.entitlement_overrides: an operator's per-tenant limits over the plan of the tenant's
edition (contract entitlements 0.1, ADR-0043)

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("entitlement_overrides", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("entitlement_overrides")
