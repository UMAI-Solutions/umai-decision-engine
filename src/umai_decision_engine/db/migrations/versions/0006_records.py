"""decision_events, l1b_outcomes, usage_events: records, append-only with row-level security
(ADR-0041, ADR-0025; contracts tagged-action 0.3 and usage 0.1)

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from umai_decision_engine.db.rls import enable_rls_sql, grant_append_only_sql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = "umai_app"  # created by deploy/postgres-init.sql; grants skipped when absent
# The tenant tables this revision creates (fixed: later revisions add their own).
TENANT_TABLES = ("decision_events", "l1b_outcomes", "usage_events")


def _tenant_column() -> sa.Column:
    return sa.Column(
        "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "decision_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("key_id", sa.String(8), nullable=True),
        sa.Column("environment", sa.String(8), nullable=True),
        sa.Column("application_id", sa.String(64), nullable=True),
        sa.Column("gateway_call_id", sa.String(64), nullable=True),
        sa.Column("bundle_id", sa.String(128), nullable=False),
        sa.Column("bundle_version", sa.Integer(), nullable=True),
        sa.Column("bundle_sha256", sa.String(64), nullable=True),
        sa.Column("bundle_mode", sa.String(16), nullable=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(16), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("floor_action", sa.String(32), nullable=True),
        sa.Column("would_have", sa.String(32), nullable=True),
        sa.Column("review", sa.Boolean(), nullable=False),
        sa.Column("review_reasons", sa.JSON(), nullable=False),
        sa.Column("subject", sa.JSON(), nullable=True),
        sa.Column("destination", sa.JSON(), nullable=True),
        sa.Column("agent_action", sa.JSON(), nullable=True),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("matched", sa.JSON(), nullable=False),
        sa.Column("l1b", sa.JSON(), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("latency_total_ms", sa.Float(), nullable=False),
        sa.Column("latency_l0_ms", sa.Float(), nullable=True),
        sa.Column("latency_l1b_ms", sa.Float(), nullable=True),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_decision_events_tenant_event"),
    )
    op.create_index(
        "ix_decision_events_tenant_occurred", "decision_events", ["tenant_id", "occurred_at"]
    )
    op.create_table(
        "l1b_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bundle_id", sa.String(128), nullable=False),
        sa.Column("bundle_version", sa.Integer(), nullable=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("enforcement", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("action", sa.String(32), nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("rule", sa.Integer(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("review_reasons", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "event_id", "policy_id", name="uq_l1b_outcomes_tenant_event_policy"
        ),
    )
    op.create_index("ix_l1b_outcomes_tenant_occurred", "l1b_outcomes", ["tenant_id", "occurred_at"])
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meter", sa.String(32), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("key_id", sa.String(8), nullable=True),
        sa.Column("environment", sa.String(8), nullable=True),
        sa.Column("application_id", sa.String(64), nullable=True),
        sa.Column("bundle_id", sa.String(128), nullable=True),
        sa.Column("bundle_version", sa.Integer(), nullable=True),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "event_id", "meter", name="uq_usage_events_tenant_event_meter"
        ),
    )
    op.create_index(
        "ix_usage_events_tenant_meter_occurred",
        "usage_events",
        ["tenant_id", "meter", "occurred_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            for statement in enable_rls_sql(table):
                op.execute(statement)
        # Append-only for the application role (ADR-0030 §3): no UPDATE, no DELETE; retention
        # runs with the owner role.
        grants = " ".join(f"EXECUTE '{grant_append_only_sql(t, APP_ROLE)}';" for t in TENANT_TABLES)
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"{grants} END IF; END $$"
        )


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_table("l1b_outcomes")
    op.drop_table("decision_events")
