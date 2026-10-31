# Architecture decision records

One file per decision, numbered, never renumbered. Change a decision by writing a new ADR
that supersedes the old one; update the old one's status line only.

Status meanings: **Accepted** — decided by the product owner; build against it.
**Proposed** — design recommendation not yet confirmed; build against it unless it is
listed in [`../open-questions.md`](../open-questions.md) as blocking, and flag deviations.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-layered-l0-l1-l2-pipeline.md) | Layered L0 → L1 → L2 pipeline | Accepted |
| [0002](0002-l2-synchronous.md) | L2 runs synchronously | Superseded by 0010 |
| [0003](0003-l2-conditional-escalation.md) | L2 runs only for escalated actions | Superseded by 0010 |
| [0004](0004-decision-model-provider-abstraction.md) | L1b uses on-prem deployable decision models behind a provider interface | Accepted (default model pending F0) |
| [0005](0005-no-raw-pii-beyond-l0.md) | Raw PII never leaves L0 | Proposed (amended by 0035) |
| [0006](0006-l2-override-rules.md) | What L2 may override | Superseded by 0010 |
| [0007](0007-presidio-for-l0.md) | Microsoft Presidio for L0 | Accepted |
| [0008](0008-standalone-rnd-repository.md) | Standalone R&D repository, English documentation | Accepted |
| [0009](0009-python-stack.md) | Python 3.12 / FastAPI / pip + pyproject | Proposed |
| [0010](0010-l2-asynchronous-review.md) | L1 decides; L2 reviews asynchronously | Accepted |
| [0011](0011-l0-does-not-decide.md) | L0 detects, it does not decide | Superseded by 0014 |
| [0012](0012-signed-immutable-policy-bundles.md) | Policies are signed, immutable bundles; the engine only reads them | Accepted |
| [0013](0013-policy-lifecycle-dashboard.md) | Policies are edited and published live from a dashboard | Accepted (direction; not built) |
| [0014](0014-l0-detection-is-terminal.md) | An L0 detection is terminal: L0 decides, L1 does not run | Accepted (§1, §5 amended by 0036) |
| [0015](0015-policy-golden-tests.md) | Policy changes are gated by golden tests that run through the real pipeline | Accepted |
| [0016](0016-control-center-separate-repo.md) | The engine's console is a separate repository that calls the API over HTTP with a service token | Accepted |
| [0017](0017-l0-coverage-lakera-parity-tr-eu.md) | L0 coverage target: Lakera PII parity, extended to Türkiye and Europe | Accepted |
| [0018](0018-saas-and-self-hosted-editions.md) | UMAI ships as cloud SaaS and self-hosted, in Community and Enterprise editions | Accepted |
| [0019](0019-editions-as-runtime-entitlements.md) | Editions are runtime entitlements: tenant keys, signed licences, engine-enforced limits | Proposed |
| [0020](0020-deterministic-baselines-names-addresses.md) | Names and addresses start with deterministic recognizers; NER is added on evidence | Accepted (NER deferred) |
| [0021](0021-guardrail-model-policies-pinning-hierarchy.md) | Guardrail model: versioned policies pinned into guardrails; company → project → guardrail | Accepted |
| [0022](0022-policy-service-control-plane.md) | Policy service: drafts, tests and publishing from the console | Accepted |
| [0023](0023-identity-planes-and-api-keys.md) | Two identity planes: per-tenant API keys for workloads, people through the console | Proposed |
| [0024](0024-human-identity-oidc-keycloak.md) | Human identity: standard OIDC, Auth0 for UMAI Cloud, Keycloak for self-hosted, broker interface | Proposed (§4 decided 2026-10-18) |
| [0025](0025-tenant-isolation-postgres-rls.md) | Tenant isolation: Postgres per region with RLS, per-tenant envelope encryption | Accepted (RLS store implemented 2026-10-18) |
| [0026](0026-region-pinning-and-global-directory.md) | Region pinning: independent regional stacks, content-free global directory | Proposed |
| [0027](0027-key-custody-kms-and-secrets.md) | Key custody: signing keys in Key Vault behind a Signer interface, secrets out of files | Proposed (amended by 0031) |
| [0028](0028-metering-entitlements-and-billing.md) | Metering, entitlements and billing: own ledger, Redis counters, billing adapter | Proposed |
| [0029](0029-authorization-roles-and-four-eyes.md) | Authorization: tenant roles enforced in services, four-eyes for production activation | Proposed |
| [0030](0030-audit-events-and-structured-telemetry.md) | Audit and decision events: append-only per tenant, exportable; telemetry allowlist | Proposed |
| [0031](0031-umai-cloud-on-azure.md) | UMAI Cloud on Azure: West Europe / North Europe, ECDSA P-256 in Key Vault Premium, Flexible Server, Application Gateway (no Front Door), AKS + Notation, Managed Redis | Proposed |
| [0032](0032-single-vm-starter-on-azure.md) | Single-VM starter on Azure: Docker Compose (Caddy, engine, policy service, console), Key Vault (or local-file) secrets, images shipped over SSH, ≈ $41–45/month; demos and pilots only | Accepted |
| [0033](0033-organizations-and-onboarding.md) | Organizations and onboarding: the tenant store owns organizations, the identity service mirrors them; invitations, verified domains, last owner, region, first key | Accepted (2026-10-18; amended 2026-10-19: store first; open points Q41–Q44) |
| [0034](0034-umai-secure-gateway.md) | UMAI secure gateway: OpenAI-compatible pass-through proxy in this repository (FastAPI, httpx, in-process pipeline), guardrails in and out, routes as tenant configuration, pass-through provider keys; no embedded gateway product | Accepted (2026-10-19; not built) |
| [0035](0035-raw-content-zone-l0-and-gateway.md) | The raw-content zone is L0 and the gateway forwarder; raw content leaves only to the caller and the route's upstream, memory only | Accepted (2026-10-19) |
| [0036](0036-checks-continue-after-masking.md) | Masking does not end the evaluation: attack and agent-action checks run on the masked text; only L0 `BLOCK` / `STEP_UP_APPROVAL` are terminal | Accepted (2026-10-19; in effect with L1b since 2026-10-20) |
| [0037](0037-per-message-l0-scanning-and-cache.md) | L0 scans a multi-part target one message at a time with a per-process detection cache (types, spans, value hashes; never text), so a resent conversation is not rescanned; the gateway gets its own input limit (256 000 characters) | Accepted (2026-10-20) |
| [0038](0038-gateways-instead-of-model-routes.md) | Gateways instead of per-model routes: one provider connection and one guardrail per gateway, models pass through, keys bind to a gateway (amends ADR-0034 §6) | Accepted (2026-10-20) |
| [0039](0039-applications-and-connections.md) | Applications as the protected unit, one guardrail per application on every path; the AI gateway as one way to connect, with its own providers (`@provider/model`); console organized as Protect, Investigate, Connect (amends ADR-0038) | Accepted (2026-10-20) |
| [0040](0040-l1b-decision-policies-on-openai-decisions.md) | L1b runs a guardrail's decision policies through a provider adapter, first the OpenAI Decisions API: redacted input only, one batched call by default, probability thresholds, shadow before enforce; the colleagues' ten policies converted to nine. Amendment 2026-10-26: a synthetic warm-up decision at startup, 120 s keep-alive, one deadline per call. Amendment 2026-10-30: the request path asks only the enforcing policies; shadow policies always in their own call after the decision | Accepted (2026-10-20; actions per policy pending) |
| [0041](0041-records-decision-events-and-usage-ledger.md) | Records v1: engine and gateway write decision events, L1b outcomes and usage records to the tenant store (append-only, RLS); no content, not even redacted; 30 days of decisions by default, 400 days of usage; accepts ADR-0030 §1, §3–§4 and ADR-0028 §1–§2 | Accepted (2026-10-26) |
| [0042](0042-records-write-path-in-memory-queue.md) | Records write path: a bounded in-memory queue and batched inserts off the request path; drop and count when full, never delay a decision (amends ADR-0030 §3 for engine and gateway) | Accepted (2026-10-26) |
| [0043](0043-plans-and-monthly-quotas.md) | Plans and monthly quotas: limits per edition from a plans file, operator overrides per tenant, carried in the signed tenant snapshot; requests (evaluate calls + gateway calls) counted per process from the usage ledger; 429 `quota_exceeded`, no grace margin; test keys apart; accepts ADR-0019 §3–§4 and ADR-0028 §2–§3 (single node) | Accepted (2026-10-30) |
| [0044](0044-billing-with-iyzico-subscriptions.md) | Billing for UMAI Cloud with iyzico subscriptions: hosted checkout form only, our subscription record decides the edition, webhooks and the callback only trigger a re-read, reconciliation, 14-day grace with retries, cancel at period end; supersedes ADR-0028 §5 for the cloud | Accepted (2026-10-30) |
| [0045](0045-audit-log-for-configuration-changes.md) | Audit log: one event per policy-service change and per refused attempt (403), append-only per tenant (RLS), actor as subject and user id (no e-mail stored), before/after of non-secret fields; list, filters and CSV export behind `audit.read`; 400 days; accepts ADR-0030 §1, §2 (narrowed), §4–§5 for audit events | Accepted (2026-10-31) |
| [0046](0046-api-key-network-restrictions.md) | API key network restrictions: allowed networks (CIDRs) per key, 403 `ip_not_allowed` at admission in the engine API and the gateway; the client address from `X-Forwarded-For` of trusted proxies only (default none; Caddy on the VM); bad credentials throttled per source (IPv4 address, IPv6 /64), 429 `too_many_failed_attempts` before any lookup | Accepted (2026-10-31) |
| [0047](0047-data-access-search-csv-and-organization-export.md) | Data access for organizations: decision search by id, entity-type and policy filters (no index, measured), decisions and usage CSV refused above a cap instead of cut, owner-only organization archive (ZIP of JSON files, 250 000 records, synchronous); erasure out of scope (billing records' statutory retention) | Accepted (2026-10-31) |
| [0048](0048-outbound-webhooks-and-slack.md) | Outbound webhooks and Slack: events read from the stored records by a worker in the policy service (blocked decisions aggregated per window, quota thresholds, billing changes), idempotent per endpoint and key; HMAC-SHA256 signatures with secrets derived from a deployment master key (not stored), URLs encrypted at rest; SSRF guard that connects to the address it checked; retries 1 m–6 h; turned off after 24 h of failures or on 410 | Accepted (2026-10-31) |
| [0049](0049-supply-chain-checks-in-ci.md) | Supply-chain checks in CI for engine and console: Dependabot weekly (grouped, 7-day cooldown), `pip-audit` / npm audit gate, gitleaks over the history, image built and scanned with Trivy (fixable HIGH/CRITICAL fail) with a CycloneDX SBOM artifact; actions pinned by SHA, scanners and base images by digest; accepted findings carry a reason and an expiry ≤ 90 days | Accepted (2026-10-31) |
| [0054](0054-customer-api-documentation.md) | Customer API documentation: a static site built from this repository (hand-written pages in English and Turkish, a reference generated from an allow-listed OpenAPI document per surface, one test holding code, contract versions and error catalogue together), built in CI and served at `/docs/` on the UMAI address; FastAPI's own `/docs` stays off | Proposed |

New ADR: copy [`0000-template.md`](0000-template.md), or use skill `write-adr`.
