"""users, memberships, invitations; organization columns on tenants (ADR-0033)

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from umai_decision_engine.db.rls import enable_rls_sql, grant_app_role_sql, self_read_sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "umai_app"
TENANT_TABLES = ("memberships", "invitations")  # fixed for this revision
SELF_READ_TABLES = ("memberships",)


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(
            sa.Column("edition", sa.String(16), nullable=False, server_default="community")
        )
        batch.add_column(sa.Column("created_by", sa.Uuid(), nullable=True))

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_invitations_tenant_id", "invitations", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            for statement in enable_rls_sql(table):
                op.execute(statement)
        for table in SELF_READ_TABLES:
            op.execute(self_read_sql(table))
        grants = " ".join(
            f"EXECUTE '{grant_app_role_sql(t, APP_ROLE)}';" for t in (*TENANT_TABLES, "users")
        )
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"{grants} END IF; END $$"
        )


def downgrade() -> None:
    op.drop_table("invitations")
    op.drop_table("memberships")
    op.drop_table("users")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("created_by")
        batch.drop_column("edition")
