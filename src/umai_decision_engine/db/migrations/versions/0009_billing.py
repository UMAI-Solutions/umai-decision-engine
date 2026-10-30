"""billing_accounts, subscriptions, billing_checkouts, billing_events (tenant tables, RLS;
billing_events append-only) and the global billing_links (contract billing 0.1, ADR-0044)

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from umai_decision_engine.db.rls import enable_rls_sql, grant_app_role_sql, grant_append_only_sql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = "umai_app"  # created by deploy/postgres-init.sql; grants skipped when absent
# The tenant tables this revision creates (fixed: later revisions add their own).
TENANT_TABLES = ("billing_accounts", "subscriptions", "billing_checkouts", "billing_events")
UPDATABLE = ("billing_accounts", "subscriptions", "billing_checkouts")


def _tenant_column() -> sa.Column:
    return sa.Column(
        "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _at(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "billing_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("tax_number", sa.String(11), nullable=False),
        sa.Column("tax_office", sa.String(100), nullable=False),
        sa.Column("contact_name", sa.String(100), nullable=False),
        sa.Column("contact_surname", sa.String(100), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("phone", sa.String(16), nullable=False),
        sa.Column("address", sa.String(300), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("zip_code", sa.String(20), nullable=True),
        _at("updated_at"),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_billing_accounts_tenant"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("parent_reference", sa.String(64), nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("customer_reference", sa.String(64), nullable=True),
        sa.Column("pricing_plan_reference", sa.String(64), nullable=False),
        sa.Column("edition", sa.String(16), nullable=False),
        sa.Column("pending_edition", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_status", sa.String(16), nullable=True),
        _at("started_at"),
        _at("current_period_end"),
        _at("cancel_at"),
        _at("grace_until"),
        _at("next_retry_at"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_order_reference", sa.String(64), nullable=True),
        sa.Column("last_payment_error", sa.String(200), nullable=True),
        _at("created_at"),
        _at("updated_at"),
        sa.UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),
    )
    op.create_table(
        "billing_checkouts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("checkout_id", sa.String(64), nullable=False),
        sa.Column("token", sa.String(512), nullable=True),
        sa.Column("edition", sa.String(16), nullable=False),
        sa.Column("pricing_plan_reference", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        _at("created_at"),
        _at("expires_at", nullable=False),
        _at("completed_at"),
        sa.UniqueConstraint("tenant_id", "checkout_id", name="uq_billing_checkouts"),
    )
    op.create_table(
        "billing_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        _at("occurred_at"),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(64), nullable=True),
        sa.Column("subscription_reference", sa.String(64), nullable=True),
        sa.Column("order_reference", sa.String(64), nullable=True),
        sa.Column("amount", sa.String(16), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "provider_event_id", name="uq_billing_events_provider_event"
        ),
    )
    op.create_index(
        "ix_billing_events_tenant_occurred", "billing_events", ["tenant_id", "occurred_at"]
    )
    for table in TENANT_TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    op.create_table(
        "billing_links",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("reference", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _at("created_at"),
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            for statement in enable_rls_sql(table):
                op.execute(statement)
        grants = [grant_app_role_sql(t, APP_ROLE) for t in UPDATABLE]
        grants.append(grant_append_only_sql("billing_events", APP_ROLE))
        # Global: the application role links references and reads them, nothing else.
        grants.append(f"GRANT SELECT, INSERT ON billing_links TO {APP_ROLE}")
        statements = " ".join(f"EXECUTE '{g}';" for g in grants)
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"{statements} END IF; END $$"
        )


def downgrade() -> None:
    op.drop_table("billing_links")
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
