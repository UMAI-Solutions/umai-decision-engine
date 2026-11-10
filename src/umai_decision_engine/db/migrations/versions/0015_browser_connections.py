"""Browser connections: api_keys.client, user_id, device_label; decision_events.user_id
(credentials 0.10, tagged-action 0.8, ADR-0058)

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only (ADR-0052): NULL for every existing key and event, which an engine of the
    # previous release neither reads nor writes.
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("client", sa.String(16), nullable=True))
        batch.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("device_label", sa.String(120), nullable=True))
    op.create_index("ix_api_keys_tenant_user", "api_keys", ["tenant_id", "user_id"])
    with op.batch_alter_table("decision_events") as batch:
        batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
    op.create_index(
        "ix_decision_events_tenant_user", "decision_events", ["tenant_id", "user_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_decision_events_tenant_user", table_name="decision_events")
    with op.batch_alter_table("decision_events") as batch:
        batch.drop_column("user_id")
    op.drop_index("ix_api_keys_tenant_user", table_name="api_keys")
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("device_label")
        batch.drop_column("user_id")
        batch.drop_column("client")
