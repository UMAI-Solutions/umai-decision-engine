# ADR-0018 — UMAI ships as cloud SaaS and self-hosted, in Community and Enterprise editions

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** product owner (UMAI)

## Context

The product owner requires UMAI to be usable both as a **cloud SaaS** and as an **on-prem,
self-hosted** installation, with the feature capabilities of Lakera Guard's plan table
(Community and Enterprise; vendor facts in [`reference/lakera-guard.md`](../reference/lakera-guard.md)).

What the engine does today (2026-10-17):

- It is on-prem first: the L1b default must run on a customer's server (ADR-0004), bundles are
  signed files that work without internet (ADR-0012).
- It is effectively **single-tenant**: one shared bearer token (ADR-0016), `tenant_id` taken
  from the request body, bundles not owned by a tenant. Any token holder can evaluate against
  any tenant's bundle.
- No metering, no request quota, no input-size limit (L0 scans any length in chunks of
  `l0.max_chars`), no version endpoint, no event export.
- The console (`umai-control-center`, ADR-0016) has no SSO and no roles.

## Decision

1. **Two deployment modes, one product.**
   - **UMAI Cloud (SaaS):** multi-tenant, operated by UMAI, one independent stack per region
     (EU, US). A tenant lives in exactly one region.
   - **Self-hosted:** the customer runs the same release on its own servers (on-prem or its
     own cloud account), including single-server, air-gapped installations. Nothing at
     runtime requires a connection to UMAI.
2. **Two editions** with exactly these capabilities:

   | Capability | Community | Enterprise |
   |---|---|---|
   | Price | Free ($0 / month) | By contract |
   | Requests included | 10 000 / month | Flexible (by contract) |
   | Maximum prompt size | 8 000 tokens | Configurable |
   | Hosting | SaaS | SaaS or self-hosted |
   | Support | Community | Enterprise-level support |
   | API | ✓ | ✓ |
   | Dashboards | ✓ | ✓ |
   | Reports | ✓ | ✓ |
   | SSO | — | ✓ |
   | Role-based access control | — | ✓ |
   | SIEM integration | — | ✓ |
   | Version pinning | — | Self-hosted deployments only |
   | Data encryption in transit and at rest | ✓ | ✓ |
   | SOC 2, GDPR compliant | ✓ | ✓ |
   | Data residency | EU | EU / US |

3. **One codebase, one release artifact** for every edition and mode. Editions differ only
   in entitlements (limits and enabled features) loaded as data at runtime — never in
   builds, forks or code paths compiled in or out. Mechanism: ADR-0019.
4. The table is a **product target, not yet a customer-facing claim.** A row is offered to
   customers only when it is built and verified — and, for SOC 2, audited. Owner and status of
   every row: [`architecture/deployment-and-editions.md`](../architecture/deployment-and-editions.md).

## Alternatives considered

| Option | Why not |
|---|---|
| SaaS only | Fails the on-prem requirement (ADR-0004) and customers who may not send data abroad (KVKK) |
| Self-hosted only | No free, self-serve entry point; every trial becomes an installation project |
| Separate codebase or build per edition (open core) | Two artifacts to test, sign and release; features drift; the editions differ in limits and integrations, which are configuration |
| A third, paid self-serve tier | Not requested; can be added later as another set of entitlements without code changes |
| A Türkiye SaaS region | Not in the requested table; Turkish customers that need in-country processing use self-hosted for now (open question Q21) |
| Community edition self-hosted | Not in the requested table; free installations carry support and licence cost |

## Consequences

- **SaaS needs real multi-tenancy** in the engine: tenant identity from the credential,
  tenant-owned bundles, tenant-scoped stores, isolation tests. Until that exists, one engine
  deployment serves one customer only.
- The engine meters requests and enforces the request quota and the prompt-size limit
  (ADR-0019).
- Data residency constrains providers: a region may only call L1b / L2 providers that process
  in that region. Community (EU) cannot use US-hosted Jev ([`reference/typesafe-jev.md`](../reference/typesafe-jev.md), Q4).
- New deliverables: per-tenant API keys, entitlements and licence files, usage and version
  endpoints, SIEM export, regional SaaS stacks, a signed container release with packaging for
  self-hosted installs (roadmap phase F6).
- The console (`umai-control-center`) must provide dashboards and reports in every edition,
  SSO and RBAC for Enterprise, and must itself be installable for self-hosted customers.
  Where the policy control plane runs and who signs bundles on-prem (Q17, Q18) now block
  self-hosted Enterprise.
- Outside the code: a SOC 2 audit, GDPR (and KVKK) processing terms with a sub-processor list,
  and a support process are company programs; the engine supplies controls and evidence for
  them, not the certification.

## Validation

Every row of the table has an owner, a check and a status in
[`architecture/deployment-and-editions.md`](../architecture/deployment-and-editions.md); the
roadmap's F6 exit criteria cover the engine rows. Revisit when pricing adds a tier, or when a
customer market needs another region.
