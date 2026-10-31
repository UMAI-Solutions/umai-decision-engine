# Launch plan: from a single-tenant R&D engine to UMAI Cloud

**Status:** 2026-10-17, proposal awaiting product-owner decisions (§6); revised the same day for
**Azure** (ADR-0031, [Azure brief](azure-brief.md)). Based on the [threat model](threat-model.md),
the [gap analysis](gap-analysis.md) and ADR-0023 to ADR-0031.
Each stage ends with a **production readiness review (PRR)**: a table where every row has a
control, the evidence that proves it, an owner and a status. Nothing ships while a row of its
stage is open. Evidence conventions: [verification plan](verification-plan.md) §3.

## 1. What "tomorrow" can honestly mean

The current build is a single-tenant engine with one shared token and a console without login.
It can serve **one customer on one dedicated deployment** today. It cannot serve two. So:

| Stage | Who is on it | What it is | What it is not |
|---|---|---|---|
| **S0 — Private beta** | 2–3 design partners under NDA and a beta agreement, EU region only | One regional stack, per-tenant API keys, console behind login, limits, audit of changes, TLS, monitoring, incident plan | Not self-serve, no SSO/SCIM, no billing, no public claims about compliance |
| **S1 — Self-serve Community** | Anyone, EU region; Enterprise by sales, EU or US | Tenant onboarding, Postgres with RLS, decision events and dashboards, metering and Stripe, DR drill done, SOC 2 program started, pentest #1 | Not "SOC 2 compliant" (no report yet), SSO still manual |
| **S2 — Enterprise GA** | Enterprise contracts with security review | SSO (SAML/OIDC) and SCIM verified against Okta and Entra ID, four-eyes approvals, SIEM export, SOC 2 Type I report, pentest #2, signed DPA template | Type II arrives after its observation window |

Owners are roles: **eng** (engineering), **po** (product owner), **ops** (whoever runs the
stack; today eng), **company** (legal, finance, people).

## 2. Stage 0 — private beta

**Entry:** ADR-0023, 0024 (§1–3, §8), 0027 (§7–8), 0030 (§1–2, §6–7) and 0031 accepted; the EU
region pair confirmed (P2).

**Scope (engineering):**

1. Per-tenant API keys with scopes, hashed storage, revocation, key-registry snapshot; body
   `tenant_id` must match the key (ADR-0023). Readiness T1 goes green.
2. Prompt-size limit (413) and per-key rate limit (429); ingress body cap (ADR-0019 §4,
   ADR-0023 §7). Readiness T2 goes green.
3. Custom validation-error handler: `loc` and `type` only (GAP-L1 goes green). **Done
   2026-10-19** (evaluation API 0.8, `api/errors.py`).
4. Console behind OIDC login (the EU Auth0 tenant, ADR-0024 §4; e-mail + password + TOTP for
   beta users), sessions and
   headers per ADR-0024 §2; on-behalf-of assertion to the policy service (ADR-0023 §1).
5. Audit events with actor for every policy-service change and key lifecycle event
   (ADR-0030 §1–2); readiness A1 goes green. JSON logs with the allowlist; `/docs` and
   `/openapi.json` disabled outside development.
6. Secrets in Key Vault, read through Entra Workload ID; signing through the `Signer` interface
   against Key Vault Premium (`kms_azure`, ECDSA P-256) with the manifest `alg` field and the
   dual-algorithm verifier (ADR-0031 §2); `cli keygen` no longer prints private keys (ADR-0027).
   Tenant HMAC keys generated per tenant, wrapped with `RSA-OAEP-256`.
7. Containers in ACR signed with Notation (Key Vault certificate; cosign in addition only if P11
   says so), SBOM (CycloneDX), `pip-audit`, Trivy and Dependabot in CI; Defender for Containers
   per P11 (Defender for Cloud is outside the EU Data Boundary); branch protection on `main`
   with required reviews.
8. Infrastructure as code for one EU regional stack — West Europe primary, North Europe DR, or
   the Sweden Central fallback (ADR-0031 §1): AKS; Application Gateway WAF_v2 with
   `AppGwSslPolicy20220101S` and HSTS by rewrite (no Front Door on content hostnames);
   PostgreSQL Flexible Server 17/18 with customer-managed key and geo-redundant backup set at
   creation (for API keys and drafts, already with the RLS pattern); Azure Managed Redis; Key
   Vault Premium; Blob Storage; private endpoints and VNet integration.
9. Observability in Azure Monitor / Log Analytics (regional): metrics (request rate, latency per
   layer, errors, 4xx/5xx, quota hits) with tenant id on counters; alerts; on-call rotation;
   status page; incident response runbook with severity levels and customer notification
   template.

**Scope (company):** beta agreement with data-processing terms (processor obligations, Art. 28
GDPR), a sub-processor list for the EU stack (incl. Auth0, ADR-0026 §5), security contact and vulnerability disclosure
address, support channel for the partners.

**PRR S0:**

| # | Control | Evidence | Owner | Status |
|---|---|---|---|---|
| S0-1 | Tenant from credential; cross-tenant evaluate refused | readiness T1, T3 green in CI | eng | **done** (2026-10-18; tenant-owned bundles and per-tenant listing 2026-10-19) |
| S0-2 | Keys hashed, scoped, revocable; revocation effective ≤ 10 s | readiness T4, T5; revocation latency measurement | eng | open |
| S0-3 | Size and rate limits with explicit 413/429; ingress body cap | readiness T2, T6; load test report | eng | partial (2026-10-19: T2 green, 413 `input_too_large`; per-key 429 since 2026-10-18; ingress cap on the VM only; load test open) |
| S0-4 | No raw content in responses, logs or errors | readiness C1, L1 green; leak suite 0 | eng | partial (2026-10-19: L1 green, 422 without input; leak suite over error bodies open) |
| S0-5 | Console requires login; sessions and headers per ADR-0024 §2 | browser tests; header scan (observatory-style) report | eng | partial (2026-10-18: OIDC login and 60 min / 12 h cookie session in `umai-control-center`; 2026-10-19: nonce-based CSP with `frame-ancestors 'none'`, HSTS, `no-store` on API answers, `Origin` check on changing calls; Caddy only fills in headers the upstream did not set; 2026-10-30: revocable sessions, a record per sign-in in the policy service checked on every call and by the console every 30 s, sessions view with "Sign out other devices" (ADR-0024 §2 amendment); admin termination, header-scan report and automated browser tests open) |
| S0-6 | MFA for owner/admin | Auth0 MFA policy screenshot + test | eng | open |
| S0-7 | Every configuration change has an actor; audit log visible in console | readiness A1; route test E1 | eng | partial (2026-10-19: A1 green, change log names the actor from the on-behalf-of assertion; 2026-10-30: audit events for every policy-service change and refused attempt, `GET /v1/audit-events` and CSV export, console Organization → Audit log, ADR-0045 Proposed; E1's hash chain open) |
| S0-8 | Secrets in Key Vault via Workload ID; signing keys in Key Vault Premium (P-256); no private keys on hosts | deployment audit record (ADR-0027 Validation); Key Vault diagnostic log sample | ops | open |
| S0-9 | TLS 1.2+/1.3 with HSTS at Application Gateway WAF_v2; no Front Door on content hostnames; internal traffic on private endpoints | TLS scan report; IaC policy test; NetworkPolicy test | ops | open |
| S0-10 | Notation-signed images in ACR, SBOM, dependency and image scanning in CI; branch protection | CI job links; GitHub settings export | eng | partial (2026-10-30, ADR-0049: `supply-chain.yml` in both repositories with `pip-audit` / npm audit gate, gitleaks, Trivy image scan and CycloneDX SBOM artifact, weekly run; Dependabot; actions and images pinned. Open: Notation signing and ACR, branch protection with required checks and the settings export, the console's Next.js upgrade before its accepted advisories expire on 2026-11-30) |
| S0-11 | Monitoring and alerting live; on-call rotation; status page | dashboard links; on-call schedule; one alert drill | ops | open |
| S0-12 | Incident response runbook and customer notification path | document in `docs/security/`; tabletop exercise record | ops + company | open |
| S0-13 | Beta agreement with processing terms; sub-processor list; security contact | signed documents; published list | company | open |
| S0-14 | Flexible Server PITR (35 days) and geo-redundant backup enabled at creation; zone-redundant HA availability recorded; one restore test | server configuration export; restore log | ops | open |
| S0-15 | Admission latency measured and within budget | `l0-latency` report with admission | eng | open |

**Exit:** all S0 rows closed; design partners onboarded by hand; a kill switch exists (revoke
all keys of a tenant; take the stack offline) and has been exercised.

## 3. Stage 1 — self-serve Community, Enterprise by sales

**Entry:** ADR-0025, 0026, 0028, 0029 (roles, not yet four-eyes), 0030 (events, export API)
accepted; billing provider chosen.

**Scope:**

1. Postgres as the tenant-scoped store with RLS and envelope encryption for drafts, bundle
   versions, decision events, audit events, usage, keys, members, entitlements; SQLite retired
   from SaaS; `http_pull` bundle source for engines (ADR-0025). Isolation tests I1–I3 in CI.
2. Decision events through the outbox; dashboards and reports in the console; `GET
   /v1/decision-events`, `GET /v1/audit-events` (ADR-0030).
3. Tenant onboarding as one automated flow: sign-up → tenant record (region chosen) → Auth0
   organization → first owner → entitlement snapshot (Community) → first API key (ADR-0033;
   design in `architecture/sign-in-and-onboarding.md`).
4. Metering ledger, Redis quotas, Stripe adapter, usage page, reconciliation job (ADR-0028);
   quota 429 and size 413 against entitlements rather than defaults.
5. Roles `owner`/`admin`/`viewer` enforced server-side; members page (ADR-0029, Community set).
6. US region stack (East US 2, DR Central US) from the same IaC module; tenant directory and
   sign-in front door (ADR-0026); provider registry with regions (empty until L1b).
7. Retention jobs and tenant export/erasure (ADR-0025 §9, ADR-0030 §4).
8. SOC 2 program start: compliance-automation platform connected to cloud, GitHub, Auth0;
   policies written; control owners named. Pentest #1 scoped to authentication, tenancy,
   console.

**PRR S1 (additional rows):**

| # | Control | Evidence | Owner | Status |
|---|---|---|---|---|
| S1-1 | Database-enforced tenant isolation | I1–I3 green in CI against Postgres | eng | **done** for `api_keys`, `drafts` (2026-10-18); events/usage/members tables follow with their ADRs |
| S1-2 | Per-tenant encryption of confidential columns; erasure destroys keys | key inventory; erasure test | eng | open |
| S1-3 | West Europe and East US 2 stacks cannot reach each other's data; wrong-region key refused | AKS NetworkPolicy and VNet test; readiness T7 | ops / eng | open |
| S1-4 | Usage ledger = usage endpoint = invoice line on a test tenant | reconciliation report; provider invoice | eng + company | open |
| S1-5 | Quota and size enforcement from entitlements; Redis-down behaviour recorded | ADR-0019 tests; chaos test log | eng | partial (2026-10-30, ADR-0043: plans and per-tenant limits in the signed snapshot, 429 `quota_exceeded`, readiness M1 green, ledger-down behaviour tested; Redis counters and the chaos test open) |
| S1-6 | Roles enforced in services; permission matrix complete | Z1 green | eng | open |
| S1-7 | Restore drill in North Europe (geo-backup restore and read-replica promotion) within RTO | drill log with timestamps | ops | open |
| S1-8 | Pentest #1: no open critical/high | report + retest letter | external | open |
| S1-9 | CAIQ-Lite self-assessment published; SIG Lite draft ≥ 80 % answerable | the documents | company + eng | open |
| S1-10 | DPA template and records of processing (Art. 30) drafted by counsel | documents | company | open |
| S1-11 | Compliance-automation platform green on infrastructure, IdP, GitHub controls | platform dashboard | company + ops | open |

**Exit:** a stranger can sign up, get a key, evaluate, see usage and decisions, and be invoiced
correctly; two tenants in one stack pass every isolation test; the US stack exists.

## 4. Stage 2 — Enterprise GA

**Entry:** ADR-0029 four-eyes. (The identity service, formerly an entry condition, was decided on
2026-10-18: Auth0, ADR-0024 §4.)

**Scope:** SAML/OIDC SSO and SCIM per tenant with self-service setup; `approval_required` with
four-eyes activation and break-glass; SIEM export (first targets immutable Blob Storage JSON
lines and Event Hubs → Sentinel; S3-compatible for other clouds, Q22); SOC 2 Type I audit; pentest #2 including SSO, SCIM and export;
signed DPA template with sub-processor lists per region; security documentation for buyers
(architecture overview, data flow, residency, encryption, retention).

**PRR S2 (additional rows):**

| # | Control | Evidence | Owner | Status |
|---|---|---|---|---|
| S2-1 | SSO and SCIM work against Okta and Entra ID test tenants; SCIM conformance passes | recorded runs; Okta suite output | eng | open |
| S2-2 | Four-eyes enforced server-side; break-glass audited | Z2 green; audit sample | eng | open |
| S2-3 | SIEM export: at-least-once, idempotent, leak suite 0 over payloads | E2; delivery test | eng | open |
| S2-4 | SOC 2 Type I report (Security, Availability, Confidentiality) | auditor's report | company | open |
| S2-5 | Pentest #2 clean | report | external | open |
| S2-6 | SIG Lite ≥ 90 % "yes + evidence" | completed questionnaire | company + eng | open |
| S2-7 | DPA signed template, sub-processor lists per region, KVKK note for Turkish customers | counsel's documents | company | open |

**After S2:** Type II observation window (plan 6 months for the first report), annual pentest,
quarterly restore drills, access reviews each quarter (CC6.3 evidence).

## 5. Sequencing and dependencies

```text
region decision (ADR-0031 §1) ─┬─▶ IaC EU stack (AKS, App Gateway) ─▶ monitoring ─▶ S0 exit
                               ├─▶ Key Vault + Workload ID (ADR-0027/0031) ─▶ P-256 signer, alg field, key snapshots
ADR-0023 keys ───────────┼─▶ admission (tenant, scope, size, rate) ─▶ T1/T2 green
ADR-0024 login ──────────┼─▶ sessions, headers, assertion ─▶ ADR-0030 actor ─▶ A1 green
ADR-0030 events ─────────┴─▶ outbox + JSON logs + 422 handler ─▶ L1 green
S0 exit ─▶ ADR-0025 Postgres/RLS ─▶ events store, dashboards ─▶ ADR-0028 metering/Stripe ─▶ S1
S1 exit ─▶ ADR-0024 §4 SSO/SCIM ─▶ ADR-0029 four-eyes ─▶ SIEM ─▶ SOC 2 Type I ─▶ S2
```

Work that must not wait for its stage: the 422 handler, `/docs` gating and `keygen` output
(small, fix now); the RLS schema conventions (every new table gets `tenant_id` from the start,
even while SQLite is in use, so the Postgres migration is a move, not a redesign).

## 6. Decisions awaiting the product owner

| # | Decision | Options | Recommendation | Blocks |
|---|---|---|---|---|
| P1 | Cloud provider for UMAI Cloud | — | **Decided: Azure** (product-owner constraint recorded in the [Azure brief](azure-brief.md) and ADR-0031; the earlier AWS recommendation is void) | — |
| P2 | EU region pair | West Europe + North Europe · Sweden Central + read replica in North Europe | **West Europe primary, North Europe DR**; the Sweden Central fallback only if zone-redundant HA is still blocked at provisioning (ADR-0031 §1); US later East US 2 ↔ Central US; Türkiye stays self-hosted (Q21) | S0 IaC |
| P3 | SaaS identity service | — | **Decided 2026-10-18: Auth0** for UMAI Cloud, Keycloak for self-hosted (ADR-0024 §4, A10); Auth0 public cloud EU or Private Cloud on Azure is Q35. Entra External ID evaluated and rejected: no organizations, no self-service SSO, no inbound SCIM | S0 login (Q35) |
| P4 | Billing provider | Stripe · Lago · Metronome · iyzico | **Decided 2026-10-30: iyzico** subscriptions for UMAI Cloud (ADR-0044); none for self-hosted | S1 |
| P5 | Rate-limit and test-key defaults | Community 20 req/s per key; test keys 1,000/month | As proposed (ADR-0023 §7, ADR-0028 §2) | S0 |
| P6 | Retention defaults | Audit 400 days; decisions 30 days Community, 30–400 Enterprise | As proposed (ADR-0030 §4) | S1 |
| P7 | SOC 2 scope and timing | Security only · + Availability + Confidentiality; Type I before S2 | **Security + Availability + Confidentiality**, Type I before S2, Type II after 6 months | S1 program start |
| P8 | Four-eyes default | On for Enterprise, off for Community | As proposed (ADR-0029 §4) | S2 |
| P9 | Accept the nine ADRs (0023–0031) as "Accepted" | — | Accept, with P2–P12 recorded in each | all |
| P10 | SaaS signing algorithm | ECDSA P-256 in Key Vault Premium · Ed25519 via HCP Vault Dedicated · Managed HSM | **P-256 in Key Vault Premium**; Ed25519 stays for self-hosted and existing bundles (ADR-0031 §2) | S0 item 6 |
| P11 | Image signing and scanning | Notation only · Notation + cosign for self-hosted customers; Defender for Containers yes/no (outside the EU Data Boundary) | **Notation**, cosign added only when self-hosted customers ask to verify with Sigstore tooling; Trivy in CI, Defender optional (ADR-0031 §6) | S0 item 7 |
| P12 | Pin a new Entra tenant to the EU Data Boundary | Pin (irreversible; US stack gets its own tenant) · don't pin | Pin for the EU tenant if the operational cost of a second tenant for US is acceptable (ADR-0031 §11) | S0/S1 |

P1 is decided. Approving P2–P4 and P10 turns the "Proposed" ADRs into decisions and lets stage 0
start; P11–P12 are needed before the EU stack is provisioned.
