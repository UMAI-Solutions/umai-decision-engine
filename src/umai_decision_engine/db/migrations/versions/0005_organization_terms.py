"""tenants.terms_*: the terms an organization's creator accepted, by whom and when
(organizations 0.3, ADR-0033 §4)

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: organizations created before 0.3 have no record of the version they accepted.
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("terms_version", sa.String(40), nullable=True))
        batch.add_column(sa.Column("terms_accepted_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("terms_accepted_at")
        batch.drop_column("terms_accepted_by")
        batch.drop_column("terms_version")
