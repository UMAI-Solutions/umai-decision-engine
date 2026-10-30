"""audit_events: the audit log, append-only with row-level security (contract audit 0.1,
ADR-0045, ADR-0030 §3)

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from umai_decision_engine.db.rls import enable_rls_sql, grant_append_only_sql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_ROLE = "umai_app"  # created by deploy/postgres-init.sql; grants skipped when absent
TABLE = "audit_events"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_sub", sa.String(255), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_audit_events_tenant_event"),
    )
    op.create_index("ix_audit_events_tenant_occurred", TABLE, ["tenant_id", "occurred_at"])

    if op.get_bind().dialect.name == "postgresql":
        for statement in enable_rls_sql(TABLE):
            op.execute(statement)
        grant = grant_append_only_sql(TABLE, APP_ROLE)
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"EXECUTE '{grant}'; END IF; END $$"
        )


def downgrade() -> None:
    op.drop_table(TABLE)
