# ADR-0026 — Region pinning: independent regional stacks, a content-free global directory

- **Status:** Proposed (awaits product-owner approval)
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner (regions are a commercial and legal choice)
- **Builds on:** ADR-0018 (EU and US regions, a tenant lives in one region), ADR-0019 §5 (residency is a deployment property); amended by ADR-0031 (Azure regions, ingress)
- **Facts:** [`reference/azure-platform.md`](../reference/azure-platform.md) §2–§3, §8 (verified 2026-10-17)

## Context

ADR-0018 promises data residency (EU for Community, EU or US for Enterprise) and KVKK makes
in-country processing a question for Turkish customers (Q21). Residency cannot be retrofitted:
once a shared database or a global endpoint has carried content across a border, no later
architecture undoes it. The threat model lists the cloud provider / foreign jurisdiction as an
adversary class (R9). This ADR fixes what is regional, what is global, and how a request that
arrives at the wrong region fails.

## Decision

1. **Everything that touches content is regional.** Per region (EU, US; `tr` possible later by
   adding a stack): an AKS cluster with the engine fleet, policy service, console and Keycloak,
   PostgreSQL Flexible Server (ADR-0025), Azure Managed Redis for rate limits and quotas, Key
   Vault Premium (ADR-0027), Blob Storage for exports, a Log Analytics workspace, L1b/L2 model
   serving. **EU: primary West Europe, DR North Europe** (paired, both unrestricted, both inside
   the EU Data Boundary; fallback Sweden Central with a read replica in North Europe if
   zone-redundant HA is blocked at provisioning). **US later: East US 2 ↔ Central US**
   (ADR-0031 §1). Hostnames are regional: `eu.api.`, `us.api.`, `eu.console.`, `us.console.`.
   Regional stacks share no database, queue or cache; Kubernetes NetworkPolicy and VNet rules
   deny cross-region traffic between stacks.
2. **The global layer holds no content and no policy.** It consists of the **tenant
   directory** (tenant id → region, status, plan), account and billing records (company,
   contacts, invoices — ADR-0028), the sign-in front door that redirects a user to their
   tenant's regional console, the marketing site and the status page. The directory is the
   only system two regions both read.
3. **Region is part of the credential and of the entitlement.** API keys carry the region in
   their prefix (`umai_live_eu_…`, ADR-0023) so SDKs route correctly and a misrouted key fails
   at admission with HTTP 403 `wrong_region` before any content is read beyond the header. The
   signed entitlement record names the tenant's region; an engine refuses a tenant whose
   entitlement names another region (ADR-0019 §5).
4. **Providers are region-gated at bundle load.** Each provider registry entry declares where
   it processes data; a bundle whose L1b provider, fallback, shadow provider or L2 model is not
   permitted in the stack's region fails validation at load, not at request time (ADR-0019 §5).
   Hosted vendors are usable only in regions where their processing is in-region and a DPA
   exists; the sub-processor list is per region.
5. **Identity provider data is account metadata, not content.** If the human plane uses a
   SaaS identity broker (ADR-0024), names, e-mails and group memberships of console users
   leave the region to that broker; policy content, prompts and decisions do not. This is
   disclosed in the sub-processor list. Enterprise tenants that require even account metadata
   to stay in region use their own IdP directly or the self-hosted identity option.
6. **Backups and disaster recovery stay in the jurisdiction.** The DR copy of a regional
   database lives in another Azure region of the same jurisdiction (West Europe → North Europe,
   East US 2 → Central US): Postgres geo-redundant backup to the paired region (latest copy,
   enabled at creation) plus a cross-region read replica where point-in-time recovery in the
   DR region is required (ADR-0031 §4). Exports requested by a customer land in a regional
   storage account.
7. **A tenant's region is chosen at onboarding and does not change in place.** A move is an
   export–import project (new tenant in the target region, keys re-issued), never a
   replication.
8. **Observability is regional with content-free global roll-ups.** Metrics and logs are
   collected per region (Log Analytics in the region); only aggregates without tenant content
   or ids leave the region for a global dashboard. Traces never carry content (ADR-0030).
9. **Ingress is regional, never a global edge.** Content hostnames sit behind an Application
   Gateway WAF_v2 in the region (`AppGwSslPolicy20220101S`, HSTS by rewrite rule). Azure Front
   Door and Azure CDN are excluded from the EU Data Boundary — TLS would terminate at global
   points of presence — so they never front `*.api` or `*.console`; at most the content-free
   global surfaces of §2 (ADR-0031 §5). Pinning a new Entra tenant to the EU Data Boundary is
   irreversible and would give the US stack its own tenant (launch-plan P12).

## Alternatives considered

| Option | Why not |
|---|---|
| One global stack with a `region` column per tenant | Content crosses borders at the ingress and rests in one database; the promise becomes a policy, not an architecture |
| Global database, regional compute | Data at rest is what residency regulates; compute location alone does not satisfy it |
| Geo-DNS only (route by caller location) | Routes by where the *caller* is, not where the *tenant's* data must stay; a travelling employee would move the tenant |
| Active-active across regions for availability | Replication is exactly the cross-border flow residency forbids; availability comes from in-jurisdiction DR instead |
| Azure Front Door in front of every hostname | Terminates TLS at global edge points outside the EU Data Boundary (ADR-0031 §5) |
| Launch EU only, add US later | Acceptable for stage 0 (launch plan); this ADR fixes the rule so adding US is a stack, not a redesign |

## Consequences

- Infrastructure as code (Terraform/OpenTofu) is written as one **region module** instantiated
  per region, plus a small global module. Cost is roughly linear in the number of regions.
- The tenant directory and the sign-in front door become the only globally available
  components; they must stay content-free by construction and be reviewed as such.
- Onboarding gains an irreversible region choice with explicit copy in the console and in the
  order form; support gets a documented export–import procedure.
- The DPA and sub-processor list are produced per region (legal, Q25).

## Validation

- Infrastructure tests: a regional stack cannot open a connection to the other region's
  database, cache or storage account (NetworkPolicy and VNet test in CI against the IaC plan,
  and a runtime probe); no Front Door profile has a content hostname as origin; the Application
  Gateway TLS policy is `AppGwSslPolicy20220101S` (TLS scan).
- Admission tests: an EU key against the US host → 403 `wrong_region`, nothing evaluated; an
  entitlement naming another region is refused at load.
- Bundle validation test: an out-of-region provider fails at load with a clear error.
- DR drill per region: geo-backup restore and read-replica promotion in the same-jurisdiction DR
  region within the RTO, isolation tests pass on the restored server (ADR-0025).
- Review of the global layer's schema: no column may hold content, policy or decisions
  (documented allowlist, checked in code review).
- Revisit when a customer market needs another region (Q21) or when a provider changes where it
  processes data (reference docs re-verified every 30 days).
