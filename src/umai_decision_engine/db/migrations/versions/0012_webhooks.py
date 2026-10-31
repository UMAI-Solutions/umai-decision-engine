"""webhook_endpoints, webhook_messages (tenant tables, RLS) and the append-only
webhook_deliveries (contract webhooks 0.1, ADR-0048)

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from umai_decision_engine.db.rls import enable_rls_sql, grant_app_role_sql, grant_append_only_sql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

APP_ROLE = "umai_app"  # created by deploy/postgres-init.sql; grants skipped when absent
# The tenant tables this revision creates (fixed: later revisions add their own).
TENANT_TABLES = ("webhook_endpoints", "webhook_messages", "webhook_deliveries")
UPDATABLE = ("webhook_endpoints", "webhook_messages")


def _tenant_column() -> sa.Column:
    return sa.Column(
        "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _at(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("url_sealed", sa.String(4096), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        _at("enabled_since", nullable=False),
        sa.Column("disabled_reason", sa.String(16), nullable=True),
        _at("disabled_at"),
        sa.Column("secret_version", sa.Integer(), nullable=False, server_default="1"),
        _at("secret_rotated_at"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        _at("failing_since"),
        _at("last_success_at"),
        _at("last_failure_at"),
        _at("created_at"),
        sa.Column("created_by", sa.String(255), nullable=False),
        _at("updated_at"),
    )
    op.create_table(
        "webhook_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        _at("occurred_at", nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _at("next_attempt_at"),
        _at("created_at"),
        _at("updated_at"),
        sa.UniqueConstraint(
            "tenant_id", "endpoint_id", "dedupe_key", name="uq_webhook_messages_key"
        ),
    )
    op.create_index(
        "ix_webhook_messages_tenant_due",
        "webhook_messages",
        ["tenant_id", "status", "next_attempt_at"],
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        _at("attempted_at", nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(32), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        _at("next_attempt_at"),
        sa.Column("test", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_webhook_deliveries_tenant_attempted",
        "webhook_deliveries",
        ["tenant_id", "attempted_at"],
    )
    op.create_index(
        "ix_webhook_deliveries_endpoint",
        "webhook_deliveries",
        ["tenant_id", "endpoint_id", "attempted_at"],
    )
    op.create_index("ix_webhook_endpoints_tenant_id", "webhook_endpoints", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            for statement in enable_rls_sql(table):
                op.execute(statement)
        grants = [grant_app_role_sql(t, APP_ROLE) for t in UPDATABLE]
        # The delivery log is append-only (ADR-0030 §3); retention runs with the owner role.
        grants.append(grant_append_only_sql("webhook_deliveries", APP_ROLE))
        statements = " ".join(f"EXECUTE '{g}';" for g in grants)
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"{statements} END IF; END $$"
        )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
