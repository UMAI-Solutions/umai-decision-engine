# Multi-tenant isolation: AWS SaaS Lens models and PostgreSQL row-level security

**Last verified:** 2026-10-17 · **Used by:** ADR-0025 (tenant isolation), ADR-0023 (tenant context from the credential), ADR-0028 (tenant-aware metering) ·
Labels: see [README](README.md). Primary sources: AWS documentation and the PostgreSQL manual
("current" = PostgreSQL 18).

## AWS Well-Architected SaaS Lens

Current lens revision 2023-04-04. The 2020 whitepaper "SaaS Tenant Isolation Strategies" is now
labelled "for historical reference"; the live PostgreSQL guidance is AWS Prescriptive Guidance,
"Implementing managed PostgreSQL for multi-tenant SaaS".

| Model | Shape | For | Against |
|---|---|---|---|
| **Silo** | Full stack per tenant (account / VPC / database), shared identity, onboarding, metering, deployment | Compliance pushback, no noisy neighbour, cost per tenant obvious, small blast radius | Account quotas, idle cost, heavy onboarding automation, decentralised monitoring |
| **Pool** | Shared infrastructure; storage "indexed by tenant identifier" | Agility, cost, one pane of glass | Noisy neighbour, cost attribution, blast radius; "typical networking and IAM constructs cannot be relied upon", which "is not a rationale to relax isolation" |
| **Bridge** | Mix per service or tier (e.g. pooled web tier, siloed database for a premium tier running the same release) | Lets a premium tier buy isolation without a second codebase | Two operating modes to test |

Isolation tenets the lens states: isolation is not optional; authentication and authorization
are **not** isolation; enforcement must not be left to individual service developers (a shared
mechanism applies the rules); isolation is applied at run time from the tenant context in the
token; some domains require a database per tenant.

Mechanisms: tenant context bound into the identity token and propagated to every layer; for
relational pools, **row-level security** — "you cannot use IAM to scope access to data at the
row level"; the Prescriptive Guidance says RLS "is required" in the pool model, prefers a
runtime variable over a database role per tenant, and enables RLS on every tenant table.
Schema-per-tenant (bridge) and database-per-tenant (silo) are the other Postgres models.

Other lens guidance we use: instrument tenant metrics and cost per tenant (dashboards and logs
carry tenant id and tier); onboarding is one automated, repeatable process (identity → tenant
record → billing → provisioning → activation); throttle at the entry point per tier (usage
plans / per-tier keys) as the least complex noisy-neighbour control; run continuous
tenant-isolation tests.

GCP has no equivalent lens; its primary guidance is per product (GKE multi-tenancy, Cloud Run
"one project per tenant" for untrusted code).

## PostgreSQL row-level security (manual, PostgreSQL 18)

How it works:

- `ALTER TABLE … ENABLE ROW LEVEL SECURITY`; with RLS on and no policy the table is
  **default-deny**. `TRUNCATE` and `REFERENCES` are not subject to RLS.
- `CREATE POLICY … [AS PERMISSIVE|RESTRICTIVE] [FOR ALL|SELECT|INSERT|UPDATE|DELETE] [TO role]
  [USING (expr)] [WITH CHECK (expr)]`. Permissive policies OR together, restrictive AND.
  `USING` filters existing rows (rows failing it silently disappear); `WITH CHECK` validates
  new rows (failure is an error); if `WITH CHECK` is omitted, `USING` applies to both.
- **Table owners bypass RLS unless `FORCE ROW LEVEL SECURITY`** is set (default `NO FORCE`).

Pitfalls and bypasses documented in the manual:

| Pitfall | Detail | Our rule (ADR-0025) |
|---|---|---|
| Superuser and `BYPASSRLS` roles | Always bypass; `NOBYPASSRLS` is the default | Application role: not superuser, `NOBYPASSRLS`, not the table owner |
| `row_security = off` | Does **not** bypass: queries that a policy would filter fail instead (safety for `pg_dump`) | Backups run as an operator role; restores are tested |
| Referential-integrity checks | Unique, primary- and foreign-key checks bypass RLS → existence can leak through constraint errors | Surrogate UUID keys; no tenant-visible natural keys |
| `SECURITY DEFINER` functions | Run with the definer's privileges; a bypass if misused; need `SET search_path` and revoked `PUBLIC` execute | None on tenant tables |
| Non-`LEAKPROOF` functions and operators | May run before policy filters and leak values through error messages ("most casting functions include their input values in the error messages"); only superusers can mark `LEAKPROOF` | Policies reference the current row only; predicates use built-in leakproof operators; review custom functions |
| Sub-`SELECT` policies under READ COMMITTED | Can observe concurrent privilege changes | Policies are a single equality on `tenant_id` |
| Tenant context variable | `SET LOCAL` lasts to the end of the transaction (warning outside one); `set_config(name, value, true)` is the function form; `current_setting(name, missing_ok)` errors on an unset name unless `missing_ok` | `SET LOCAL app.tenant_id` inside the same transaction as the query (also what transaction-pooling proxies require); `missing_ok` **false** so an unset tenant fails loudly |
| Performance | Row-only policies are "the simplest and best-performing case"; security-barrier semantics can reject the fastest plan; an index scan is unavailable if the WHERE operator's function is not leakproof | Policies are equality on an indexed `tenant_id`; query plans checked for tenant tables |

The policy idiom `tenant_id = current_setting('app.tenant_id')::uuid` is AWS's guidance, not
PostgreSQL's; the manual supplies the pieces (`SET LOCAL`, `current_setting`, custom
two-part parameter names).

## Sources (accessed 2026-10-17)

AWS: docs.aws.amazon.com/wellarchitected/latest/saas-lens/ (saas-lens, document-revisions,
general-design-principles, isolation-mindset, silo-isolation, pool-isolation, bridge-model,
tenant-onboarding, operate, foundations, monitoring, tenant-activity-and-consumption,
noisy-neighbor) · docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/ ·
docs.aws.amazon.com/prescriptive-guidance/latest/saas-multitenant-managed-postgresql/rls.html ·
aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/ (2020-05-18).
GCP: docs.cloud.google.com/kubernetes-engine/docs/concepts/multitenancy-overview ·
docs.cloud.google.com/run/docs/securing/multi-tenant.
PostgreSQL: postgresql.org/docs/current/ddl-rowsecurity.html · /sql-createpolicy.html ·
/sql-altertable.html · /sql-createrole.html · /sql-createfunction.html · /sql-set.html ·
/functions-admin.html · /runtime-config-custom.html · /rules-privileges.html.
