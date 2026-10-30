# Verification plan: what proves each control, and who is not us

**Status:** 2026-10-17. For every control in the [launch plan](launch-plan.md) this names the
artifact that shows it holds. Two sources of truth count: tests in this repository that run in
CI, and artifacts produced by someone **other than UMAI and this agent** (identity-provider
tenants, an auditor, a penetration tester, a billing provider's invoice, a lawyer). A control
without one of these is a claim, not a control.

## 1. Automated, in this repository

| Id | Control | Test | Where | Today |
|---|---|---|---|---|
| C1 | No raw content in logs or response metadata (ADR-0005) | Evaluate synthetic PII with DEBUG logging captured; assert no raw value in logs or response | `tests/readiness/test_saas_readiness.py::test_c1_*`; `python -m evals.run --suite leak` | **green** |
| C2 | Distinct HMAC key per tenant | `Settings.tenant_key(a) != tenant_key(b)` | readiness C2 | **green** |
| T1 | Credential tenant = body tenant, else 403 (ADR-0023) | Key of tenant A, body `tenant-b` → 403 `tenant_mismatch` | readiness T1; `tests/integration/test_api_keys.py` | **green** (2026-10-18) |
| T2 | Oversized input → 413, nothing scanned (ADR-0019 §4) | 700 kB body → expect 413 | readiness T2 | **red** (xfail, GAP-T2) |
| L1 | Validation errors never echo content | Missing `timestamp` with PII in content → 422 without the value | readiness L1 | **red** (xfail, GAP-L1) |
| A1 | Every configuration change names its actor (ADR-0030) | Activate a version → history entry has `actor` | readiness A1 | **red** (xfail, GAP-A1) |
| T3 | Key of tenant A cannot evaluate as tenant B; cannot reload | Cross calls → 403 | `test_api_keys.py::test_key_of_tenant_a_cannot_act_as_tenant_b`, `::test_scopes_are_enforced_per_route` | **green** for evaluate/reload; bundle *listing* per tenant waits for tenant-owned bundles (ADR-0025 §8) |
| T4 | Revoked key refused within the poll interval | Revoke → 401 after reload | `test_api_keys.py::test_key_lifecycle_create_use_revoke` | **green** |
| T5 | Scope enforcement (`evaluate`-only key on `GET /v1/bundles` → 403) | `test_api_keys.py::test_scopes_are_enforced_per_route` | **green** |
| T6 | Per-key rate limit → 429 with `Retry-After`, no decision | `test_api_keys.py::test_rate_limit_answers_429_with_retry_after` | **green** (in-process bucket; Redis in the regional stack) |
| T7 | Wrong-region key → 403 `wrong_region` before content is read (ADR-0026) | with ADR-0026 | — |
| I1 | Schema: every tenant table has RLS **and** FORCE RLS and the isolation policy, or is allowlisted (ADR-0025) | `tests/db/test_isolation.py::test_every_table_is_tenant_scoped_with_rls_or_allowlisted` (Postgres service container in CI) | **green** (2026-10-18) |
| I2 | Generated cross-tenant tests: write as A, read as B → 0 rows; no `app.tenant_id` → **error** (never an empty page); `WITH CHECK` blocks foreign inserts | `test_isolation.py`, parametrised over `TENANT_TABLES` | **green** |
| I3 | App role: no `BYPASSRLS`, not superuser, not a member of `azure_pg_admin`, owns no tenant table | `test_isolation.py::test_application_role_cannot_bypass_rls_or_own_tenant_tables` | **green** |
| Z1 | Permission matrix: each role × each endpoint; undeclared endpoint fails (ADR-0029) | with ADR-0029 | — |
| Z2 | Four-eyes: publisher cannot activate own production version when required | with ADR-0029 | — |
| E1 | Every mutating route emits exactly one audit event; event rows are insert-only; hash chain breaks on tampering (ADR-0030) | `tests/unit/test_audit_routes.py` (declaration walk), `tests/integration/test_audit_log.py` (refused-attempt walk, events per change), `test_isolation.py` (insert-only, ADR-0045) | partial (policy service green; hash chain and engine control endpoints open) |
| E2 | Leak suite over decision events, audit events, JSON logs, span attributes, exports | extend `evals/suites/leak.py` | partial (logs, responses) |
| M1 | Metering: decision events = usage counter = metered quantity over a test period (ADR-0028) | with ADR-0028 | — |
| S1 | Dependency audit, SBOM, image scan in CI; no known critical CVE | `pip-audit`, CycloneDX, Trivy in `ci.yml` | **missing** |

Rule for the readiness suite: a gap test is `xfail(strict=True)`. When the control lands, the
test XPASSes, CI fails, and the marker is removed in the same change as the gap-analysis update.
Red tests are therefore a measurement, not a broken build.

## 2. Produced by someone else

| Control area | External artifact | Who produces it | When (launch stage) |
|---|---|---|---|
| Enterprise SSO and provisioning | SAML and OIDC sign-in and SCIM provisioning working against an **Okta developer tenant** and a **Microsoft Entra ID test tenant**; Okta's SCIM conformance test suite passing | Okta, Microsoft (their tenants, their test suite) | Stage 2 |
| Tenant isolation, authentication, authorization | **Third-party penetration test** report with no open critical/high finding; retest letter | External firm (candidates in `reference/compliance-and-assurance.md`) | Before stage 2 GA; annually |
| Operating controls | **SOC 2 Type I** report (design of controls at a point in time), then **Type II** over an observation window | Independent CPA firm | Type I before stage 2; Type II after the window |
| Buyer due diligence | A completed **SIG Lite** or **CAIQ** with "yes + evidence link" on ≥ 90 % of items; later, a real customer's questionnaire accepted | Shared Assessments / CSA formats; the customer's security team | Stage 1 draft, stage 2 complete |
| Standards conformance | **OWASP ASVS Level 2** self-assessment: every requirement mapped to a test, a control or an accepted deviation; external review of the mapping during the pentest | ASVS document; pentest firm reviews | Stage 1 → 2 |
| Billing correctness | For one billing period on a test tenant: event count = usage endpoint = **invoice line** from the billing provider | The billing provider's invoice | Stage 1 |
| Resilience | **Restore drill**: point-in-time restore into the same-jurisdiction DR region within the RTO, isolation tests green on the restored cluster; recorded with timestamps | Cloud provider's backup service; the drill log | Stage 1, then quarterly |
| Legal | **DPA** (processor terms, Art. 28 GDPR), sub-processor list per region, records of processing (Art. 30), KVKK assessment for Turkish customers | Counsel | Stage 1 (draft), stage 2 (signed template) |
| Residency | Azure region and EU Data Boundary documentation for every service in the stack (`reference/azure-platform.md`); no Front Door profile fronts a content hostname (IaC policy test); AKS NetworkPolicy test output | Microsoft Learn; IaC test | Stage 0 (EU only) |
| Supply chain | Notation-signed images in ACR (plus cosign signatures if P11 keeps them for self-hosted customers) and SBOM verifiable by the customer | Notation / Sigstore tooling; the customer verifies | Stage 1 |
| Inherited infrastructure controls | Azure's SOC 2 Type 2 reports and quarterly bridge letters for the physical and infrastructure criteria | Microsoft, via the Service Trust Portal | Stage 1 (SOC 2 program start) |

## 3. Evidence conventions

- Each PRR row in the launch plan links to its evidence: a CI job, a test id above, a file in
  `docs/security/evidence/` (reports, drill logs, questionnaire exports — never containing
  customer data), or an external report reference.
- Evidence is dated; anything older than the review cadence (quarterly for drills, annually
  for pentest and SOC 2) is stale and the row goes back to "open".
- Compliance-automation tooling (`reference/compliance-and-assurance.md`) collects the
  infrastructure, IdP and GitHub evidence continuously once the stack exists; it does not
  replace the tests above, it files them.
