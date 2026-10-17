"""Tenant-scoped store (ADR-0025): SQLAlchemy 2 (async) over PostgreSQL with row-level security
in SaaS, SQLite for development, tests and single-node self-hosted installs.

Rules enforced here and in code review:

- every tenant table has ``tenant_id`` and is listed in :data:`rls.TENANT_TABLES`; everything
  else is on the global allowlist;
- the only way to touch a tenant table is :func:`session.tenant_session`, which opens the
  transaction and sets ``app.tenant_id`` for the RLS policy; on SQLite the repositories'
  mandatory ``tenant_id`` filter is the only guard;
- the request path (engine) never imports this package: keys reach engines as signed
  snapshots (ADR-0023 §4).
"""
