# ADR-0025 — Tenant isolation: one Postgres per region with row-level security, per-tenant envelope encryption, SQLite retired from SaaS

- **Status:** Accepted (product owner, 2026-10-18). Implemented for `tenants`, `api_keys`, `drafts` (`db/`, migration 0001, `tests/db/test_isolation.py`); §4 envelope encryption and §8 bundles in the store follow
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner
- **Builds on:** ADR-0012 (signed bundles), ADR-0018 (regions), ADR-0019 §2 (tenant-owned data), ADR-0022 (policy service); amended by ADR-0031 (Azure Database for PostgreSQL Flexible Server, Key Vault wrapping)

## Context

The engine keeps no customer data at rest today: bundles are files in `policies/`, drafts are a
SQLite file (`control_plane/drafts.py`), decision events are not stored
(`review_queued=False`), the change log is a JSON-lines file. SaaS needs stores for drafts,
bundle versions, decision events, audit events, usage, API keys, members and entitlements —
all tenant-scoped. The first multi-tenant store is where cross-tenant exposure becomes
possible (threat model R3, attack tree 5.1 branch 2). Enterprise reviewers ask one question
about it: *what prevents a missing `WHERE tenant_id = ?` from returning another customer's
rows?* "Our code always filters" is not an acceptable answer; the database must refuse.

Reference material: AWS SaaS Lens isolation models (silo, pool, bridge; background — the
deployment is Azure) and the PostgreSQL Row Security Policies documentation, summarised with
sources in [`reference/saas-tenant-isolation.md`](../reference/saas-tenant-isolation.md);
Azure Database for PostgreSQL Flexible Server specifics in
[`reference/azure-platform.md`](../reference/azure-platform.md) §2.

## Decision

1. **Pool model with database-enforced isolation.** One Azure Database for PostgreSQL Flexible
   Server (version 17 or 18) per region (ADR-0026, ADR-0031 §4) holds every tenant's rows. Every tenant-scoped table has
   `tenant_id uuid NOT NULL`, `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY`
   (so even the owning role is subject to the policy), and one policy:

   ```sql
   CREATE POLICY tenant_isolation ON <table>
     USING (tenant_id = current_setting('app.tenant_id')::uuid)
     WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
   ```

   The application connects as a role that is **not** the table owner, has **no**
   `BYPASSRLS`, is not a superuser and is **not a member of `azure_pg_admin`** (Flexible
   Server's pseudo-superuser group, which can create `BYPASSRLS` roles on PG ≥ 16). Every unit of work runs inside a transaction that
   starts with `SET LOCAL app.tenant_id = :tenant` taken from the credential (ADR-0023);
   `SET LOCAL` inside the same transaction is also what transaction-pooling proxies require.
   `current_setting` is called **without** `missing_ok`, so a query that reaches a tenant table
   with no tenant set fails with an error instead of silently returning zero rows: a missing
   tenant context is a bug we want to see, not an empty page. Primary and foreign keys are
   surrogate UUIDs, because referential-integrity checks bypass RLS and natural keys could leak
   existence through constraint errors.
2. **Global tables are an explicit allowlist**: `tenants`, `regions`, `plans`, schema
   migrations. Anything else without `tenant_id` fails the schema test (Validation).
3. **Functions and operators.** No `SECURITY DEFINER` functions on tenant tables. Query paths
   that filter tenant tables use only built-in, `LEAKPROOF`-safe operators; custom functions
   used in RLS predicates or on RLS-protected columns are reviewed for leaks through error
   messages. Operator-level, audited maintenance roles exist for migrations and support, and
   never serve requests.
4. **Encryption.** Flexible Server's AES-256 at rest is table stakes, not isolation; it runs with
   a customer-managed RSA key in Key Vault (same region, plus a second key in the DR region for
   geo-backup), both chosen at server creation because they cannot be added later (ADR-0031 §3).
   In addition, Confidential columns — draft bodies, bundle YAML, redacted excerpts in decision
   events, audit `before/after` payloads — are encrypted in the application with a
   **per-tenant data key** generated in our code and wrapped by Key Vault Premium
   (`RSA-OAEP-256`; envelope encryption, ADR-0027 §6), the versioned wrapping-key id stored
   beside each wrapped key. Columns
   needed for queries (ids, entity types, counts, timestamps, HMAC hashes) are stored in clear;
   they contain no content by construction (ADR-0005). Deleting a tenant's data key is the
   cryptographic part of tenant erasure.
5. **Bridge on demand for Enterprise.** The same code can route a tenant to a dedicated
   database (connection chosen from the tenant directory). Not offered at launch; the design
   keeps it possible for customers whose security review requires it.
6. **Access layer.** SQLAlchemy 2 (async) with asyncpg, Alembic migrations. A single
   `tenant_session(tenant_id)` context manager is the only way to obtain a session for tenant
   tables; it opens the transaction and issues `SET LOCAL`. Code review rule: raw SQL against
   tenant tables outside the access layer is rejected.
7. **SQLite stays for development, tests and single-node self-hosted installs** through the
   same access layer (RLS is a Postgres guarantee; on SQLite the access layer's mandatory
   tenant filter is the only guard, acceptable for a single-tenant install). The isolation tests
   run against Postgres in CI (service container); the SaaS release ships with Postgres only.
8. **Bundles in SaaS move off the shared disk.** The control plane stores bundle versions and
   active pointers in Postgres (tenant-scoped) and serves signed artifacts; SaaS engines use an
   `http_pull` bundle source per tenant (ADR-0013 direction). `policies/` directories remain
   the format for self-hosted and development.
9. **Backups and erasure.** Backups are per server: point-in-time recovery with 35-day
   retention and geo-redundant backup to the paired region, both set at creation (the geo copy
   restores the latest backup only; a cross-region read replica is the DR path when
   point-in-time recovery in the DR region is required, ADR-0031 §4). Every tenant-level
   operation — export, erasure, retention — runs through the tenant session and is itself an
   audit event. Erasure = delete rows through RLS + destroy the tenant data key.

## Alternatives considered

| Option | Why not |
|---|---|
| Database per tenant (silo) | Migrations × N, connection pools × N, cost; offered later as the bridge option for customers who pay for it |
| Schema per tenant | Same migration burden as silo with weaker separation; `search_path` mistakes are a known cross-tenant bug class |
| Application-layer filtering only | One forgotten predicate is a breach; the database cannot refuse. Kept as defence in depth, never as the control |
| Keep SQLite in SaaS | Single writer, file-level access control, no RLS, no managed backups |
| A document or key-value store with partition keys | Isolation then lives in every query again; the team's stack and the control plane's relational data fit Postgres |
| Encrypt whole rows / everything | Breaks indexing on ids and types; the clear columns carry no content by ADR-0005 |

## Consequences

- New dependencies: SQLAlchemy 2, asyncpg, Alembic, `cryptography` (already present) for
  envelope encryption. The request path does not touch Postgres (keys and entitlements come
  from snapshots, ADR-0023/0019; events go through an outbox), so engine latency is
  unaffected; the control plane's p95 is measured but not latency-critical.
- CI gains a Postgres service container and the schema-derived isolation tests; development
  gets `docker compose` with Postgres and a seed script.
- Operations: one PostgreSQL Flexible Server per region with customer-managed keys, PITR and
  geo-redundant backup set at creation, VNet integration, a DR path in the same jurisdiction
  (ADR-0026, ADR-0031).
- Erasure, export and retention become implementable per tenant (GDPR Art. 17 and 20 requests).

## Validation

- **Schema test** (CI): every table either has `tenant_id` with `relrowsecurity` and
  `relforcerowsecurity` true and the `tenant_isolation` policy, or is in the global allowlist.
- **Generated cross-tenant tests** (CI): for every tenant table, write as tenant A, read as
  tenant B → 0 rows; read with no `app.tenant_id` → 0 rows; `WITH CHECK` rejects an insert whose
  `tenant_id` differs from the session's tenant.
- **Role test**: the application role has no `BYPASSRLS`, is not superuser, is not a member of
  `azure_pg_admin`, owns no tenant table.
- Third-party penetration test scoped to tenant isolation; no critical/high finding.
- Restore drill: restore in the DR region (geo-backup restore and read-replica promotion),
  verify a tenant's data and that the isolation tests pass on the restored server.
- Revisit if an Enterprise customer requires silo isolation (activate the bridge path) or if
  RLS predicates show up in query plans as a measurable cost on the control plane.
