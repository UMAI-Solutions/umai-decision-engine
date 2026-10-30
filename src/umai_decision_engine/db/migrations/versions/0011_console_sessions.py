"""console_sessions: one record per console sign-in, global (contract console-sessions 0.1,
ADR-0024 §2 amendment)

Revision ID: 0011
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from umai_decision_engine.db.rls import grant_app_role_sql

revision = "0011"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_ROLE = "umai_app"  # created by deploy/postgres-init.sql; grants skipped when absent


def upgrade() -> None:
    op.create_table(
        "console_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sid_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("device", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(32), nullable=True),
    )
    op.create_index("ix_console_sessions_user_id", "console_sessions", ["user_id"])
    op.create_index("ix_console_sessions_expires_at", "console_sessions", ["expires_at"])

    if op.get_bind().dialect.name == "postgresql":
        # Global (no RLS): the application role starts, ends and purges sessions.
        grant = grant_app_role_sql("console_sessions", APP_ROLE)
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"EXECUTE '{grant}'; END IF; END $$"
        )


def downgrade() -> None:
    op.drop_table("console_sessions")
