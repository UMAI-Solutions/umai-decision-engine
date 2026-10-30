"""Which tables are tenant-scoped (row-level security) and which are global (ADR-0025 §1–2).
The migration applies the policy from this list; the isolation tests check the live schema
against it, so a new tenant table that forgets RLS fails CI."""

from __future__ import annotations

POLICY_NAME = "tenant_isolation"
TENANT_SETTING = "app.tenant_id"

USER_SETTING = "app.user_id"
SELF_POLICY_NAME = "member_self"

# Tenant tables: tenant_id uuid NOT NULL, ENABLE + FORCE ROW LEVEL SECURITY, one policy.
TENANT_TABLES: tuple[str, ...] = (
    "api_keys",
    "drafts",
    "memberships",
    "invitations",
    "decision_events",
    "l1b_outcomes",
    "usage_events",
    "billing_accounts",
    "subscriptions",
    "billing_checkouts",
    "billing_events",
    "webhook_endpoints",  # ADR-0048
    "webhook_messages",
    "webhook_deliveries",
)

# Records (ADR-0041): append-only, the application role may INSERT and SELECT only (ADR-0030 §3).
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "decision_events",
    "l1b_outcomes",
    "usage_events",
    "billing_events",  # ADR-0044: the billing history
    "webhook_deliveries",  # ADR-0048: the delivery log
)

# Global tables: the explicit allowlist (ADR-0025 §2; users: ADR-0033 §2).
# billing_links: provider subscription reference → tenant, references only (ADR-0044 D4).
GLOBAL_TABLES: tuple[str, ...] = ("tenants", "users", "billing_links", "alembic_version")

# Tables with a second, read-only policy for the person's own rows (ADR-0033 §2). The only one.
SELF_READ_TABLES: tuple[str, ...] = ("memberships",)


def enable_rls_sql(table: str) -> list[str]:
    predicate = f"tenant_id = current_setting('{TENANT_SETTING}')::uuid"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {POLICY_NAME} ON {table} USING ({predicate}) WITH CHECK ({predicate})",
    ]


def self_read_sql(table: str) -> str:
    """``member_self``: a person reads their own memberships across tenants (the switcher),
    before any tenant is chosen. ``missing_ok`` here on purpose: in an ordinary tenant session
    no user is set and this policy must match nothing, while ``tenant_isolation`` stays strict
    (ADR-0025 §1). SELECT only: writes always need the tenant policy."""

    predicate = f"user_id = NULLIF(current_setting('{USER_SETTING}', true), '')::uuid"
    return f"CREATE POLICY {SELF_POLICY_NAME} ON {table} FOR SELECT USING ({predicate})"


def grant_app_role_sql(table: str, role: str) -> str:
    return f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}"


def grant_append_only_sql(table: str, role: str) -> str:
    return f"GRANT SELECT, INSERT ON {table} TO {role}"
