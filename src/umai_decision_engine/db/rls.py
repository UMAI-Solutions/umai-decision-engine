"""Which tables are tenant-scoped (row-level security) and which are global (ADR-0025 §1–2).
The migration applies the policy from this list; the isolation tests check the live schema
against it, so a new tenant table that forgets RLS fails CI."""

from __future__ import annotations

POLICY_NAME = "tenant_isolation"
TENANT_SETTING = "app.tenant_id"

# Tenant tables: tenant_id uuid NOT NULL, ENABLE + FORCE ROW LEVEL SECURITY, one policy.
TENANT_TABLES: tuple[str, ...] = ("api_keys", "drafts")

# Global tables: the explicit allowlist (ADR-0025 §2).
GLOBAL_TABLES: tuple[str, ...] = ("tenants", "alembic_version")


def enable_rls_sql(table: str) -> list[str]:
    predicate = f"tenant_id = current_setting('{TENANT_SETTING}')::uuid"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {POLICY_NAME} ON {table} USING ({predicate}) WITH CHECK ({predicate})",
    ]


def grant_app_role_sql(table: str, role: str) -> str:
    return f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}"
