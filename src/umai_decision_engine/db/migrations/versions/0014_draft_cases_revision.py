"""drafts: no base version for a new guardrail, golden cases, revision, origin (guardrails 0.1,
ADR-0056)

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing drafts keep their base version; their cases stay NULL and are read from the base
    # version's tests/ as before. Revision 1 for each.
    with op.batch_alter_table("drafts") as batch:
        batch.alter_column("base_version", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("tests", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(sa.Column("created_from", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM drafts WHERE base_version IS NULL")
    with op.batch_alter_table("drafts") as batch:
        batch.drop_column("created_from")
        batch.drop_column("revision")
        batch.drop_column("tests")
        batch.alter_column("base_version", existing_type=sa.Integer(), nullable=False)
