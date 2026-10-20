"""tenants, api_keys, drafts with row-level security (ADR-0023, ADR-0025)

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from umai_decision_engine.db.rls import enable_rls_sql, grant_app_role_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "umai_app"  # created by deploy/postgres-init.sql; grants skipped when absent
# The tenant tables this revision creates (fixed: later revisions add their own).
TENANT_TABLES = ("api_keys", "drafts")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key_id", sa.String(8), nullable=False, unique=True),
        sa.Column("secret_sha256", sa.String(64), nullable=False),
        sa.Column("env", sa.String(8), nullable=False),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_table(
        "drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("bundle_id", sa.String(128), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("bundle", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "bundle_id", name="uq_drafts_tenant_bundle"),
    )
    op.create_index("ix_drafts_tenant_id", "drafts", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            for statement in enable_rls_sql(table):
                op.execute(statement)
        # The application role never owns tables and has no BYPASSRLS (ADR-0025 §1).
        grants = " ".join(
            f"EXECUTE '{grant_app_role_sql(t, APP_ROLE)}';" for t in (*TENANT_TABLES, "tenants")
        )
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"{grants} END IF; END $$"
        )


def downgrade() -> None:
    op.drop_table("drafts")
    op.drop_table("api_keys")
    op.drop_table("tenants")
