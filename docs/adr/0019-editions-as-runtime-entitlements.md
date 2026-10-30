# ADR-0019 — Editions are runtime entitlements: tenant keys, signed licences, engine-enforced limits

- **Status:** Proposed; §3 (entitlements as signed data, carried in the tenant snapshot) and §4 (the request quota, 429 `quota_exceeded`) accepted by ADR-0043 (2026-10-30)
- **Date:** 2026-10-17
- **Deciders:** design proposal (engineering); the product decisions it implements are ADR-0018

## Context

ADR-0018 requires one release to serve Community SaaS, Enterprise SaaS and Enterprise
self-hosted, differing only in limits and features. Constraints from earlier decisions:

- Self-hosted installs may be air-gapped (ADR-0012): nothing may depend on reaching UMAI.
- No silent fail-open (AGENTS rule 4): content that was not evaluated must never look allowed.
- Raw PII never leaves L0 (ADR-0005), which also applies to anything exported to a SIEM.
- Policies are data, loaded live and signed (ADR-0012, AGENTS rule 8). Commercial limits are
  not policy, but they change for the same reasons (a contract changes, not the code).
- Today any holder of the shared token (ADR-0016) can act as any tenant: `tenant_id` comes
  from the request body and bundles have no owner.

## Decision

1. **Tenant identity comes from the credential, not from the body.**
   - SaaS: each tenant has its own API keys, issued and revoked in the console. The engine
     sees only key hashes (SHA-256) and resolves a key to `tenant_id`, key id and scopes
     (`evaluate`, `bundles:read`, `bundles:reload`). A request whose body `tenant_id` differs
     from the key's tenant is rejected with HTTP 403.
   - Self-hosted: the shared token of ADR-0016 stays valid for a single-customer install;
     per-tenant keys are optional there.
   - The console calls with a service credential that may act for a tenant and must name the
     acting console user for the audit trail.
2. **Bundles and stored data belong to a tenant.** The signed manifest names the owning
   tenant; the bundle store resolves `(tenant_id, bundle_id)` only. Decision events, session
   history, analyses and usage are keyed by the credential's tenant on write and filtered by
   it on every read. Shared templates are copied into a tenant when published, never
   referenced across tenants.
3. **Entitlements are a signed data record per tenant:** edition, deployment mode, region,
   limits (`monthly_requests`, `max_input_tokens`) and features (`sso`, `rbac`, `siem`,
   `version_pinning`). Edition values (10 000 requests, 8 000 tokens) live in this data, not
   in code constants.
   - SaaS: the control plane owns the tenant registry and publishes signed entitlement
     snapshots; the engine loads them like bundles (verify → validate → atomic swap →
     last-known-good, hot reload).
   - Self-hosted: a **licence file** is one signed entitlement record with a validity period,
     verified offline against a UMAI public key shipped in the release. No licence server,
     no phone-home.
4. **The engine enforces both limits before the pipeline runs**, with explicit outcomes and
   never a decision for content it did not evaluate:
   - **Prompt size:** measured over all request content (`content.messages[].content` and
     `content.artifacts[].content`). Over the limit → HTTP 413 `input_too_large` with the
     limit and the measured size. The engine never truncates or partially scans. Enterprise
     values are per tenant, capped by a deployment-wide maximum set from measured latency.
   - **Request quota:** every admitted `POST /v1/evaluate` counts (rejected 4xx requests do
     not; failure-policy decisions do), per tenant per calendar month (UTC). Over the limit →
     HTTP 429 `quota_exceeded` with `Retry-After` and the reset time. The caller's own
     configured failure behaviour then applies, visibly. `monthly_requests: null` means
     metered but not refused (Enterprise, self-hosted).
   - If the usage counter store fails, the engine follows a configured behaviour (default:
     admit and record the failure). The quota is a billing control, not a security control;
     the security decision still runs.
5. **Residency is a deployment property.** A SaaS stack serves one region; the engine refuses
   tenants whose entitlement names another region. Provider registry entries declare where
   they process, and a bundle whose L1b provider, fallback, shadow or L2 model is not
   permitted in the stack's region fails validation at load time, not at request time.
6. **Feature gates are checked where the feature lives:** the engine gates SIEM export; the
   console gates SSO and RBAC, reading the same entitlement record through the engine API.
   Version pinning is a property of self-hosted releases (immutable, signed images; the engine
   reports its version, L0 catalog version and model ids, and every decision event records
   the engine version). SaaS runs the current release for every tenant.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep the shared token and body `tenant_id` | Any key holder can act as any tenant; disqualifying for SaaS |
| Quota and size limits only at an API gateway | Not present in self-hosted installs; a gateway cannot tell which requests were evaluated. Per-second rate limits at the SaaS ingress remain useful in addition |
| Over quota → decision `BLOCK` | Turns a billing event into an outage of the customer's AI use, indistinguishable from a policy block |
| Over quota → decision `ALLOW` | Content that was not evaluated looks allowed: silent fail-open |
| Truncate oversized input, scan the first N tokens | The unscanned remainder is a silent fail-open |
| Licence server or usage phone-home for self-hosted | Breaks air-gapped installs (ADR-0012) |
| Edition limits as code constants | A commercial change would need a release |
| Entitlements read live from the control-plane database | Couples every request to control-plane availability; snapshots keep the engine a reader, as for bundles |

## Consequences

- Contract changes before code (skill `change-contract`): evaluation API 0.5 (403, 413, 429
  bodies; `GET /v1/usage`, `GET /v1/entitlements`, `GET /v1/version`); policy-bundle manifest
  gains the owning tenant; tagged action gains the engine version; new contracts for
  entitlements / licence and for the SIEM export record.
- New code: `tenancy/` (key resolution, entitlement source, quota, size measurement) and
  `export/` (SIEM exporter, off the request path); settings for region, entitlement source,
  licence path, counter store and the prompt-size cap.
- Admission (auth, entitlement lookup, size, quota) is on the request path: it needs a
  latency benchmark, and the counter store (Redis vs. Postgres, Q23) is chosen by it.
- How tokens are counted (Q19), licence-expiry behaviour (Q24) and SIEM targets (Q22) are
  open and block the parts that depend on them.

## Validation

- Tests: a tenant-A key cannot evaluate against, list or reload tenant B's bundles or read B's
  usage; request `limit + 1` gets 429 and nothing is evaluated; input at `limit + 1` tokens
  gets 413; a tampered or expired licence is handled as configured; a tenant of another region
  is refused; a bundle naming an out-of-region provider fails validation.
- Benchmark: admission overhead on the reference hardware, reported with `l0-latency`.
- Leak suite over SIEM export payloads: 0 raw values.
- Revisit if the gateway-only alternative becomes sufficient (e.g. if self-hosted drops
  quotas entirely) or if the counter store cannot meet the latency budget.

## Amendment 2026-10-19: prompt size in characters (Q19 answered)

**Decision.** Prompt size is counted in **characters** (Unicode code points) over all request
content, not in tokens. The entitlement limit is `max_input_chars` (Community **32 000**, about
the 8 000 tokens first planned at four characters per token), replacing `max_input_tokens`.
Until entitlement snapshots reach the engine, one deployment-wide setting enforces it
(`UMAI_DE_MAX_INPUT_CHARS`, default 32 000; evaluation API 0.9, 413 `input_too_large` with
`limit`, `size`, `unit`).

**Why.** A tokenizer is a new dependency and a model file to ship and update (AGENTS rule 6)
and counts differently per vendor; characters cost microseconds (0.6 µs for one 32 000-character
message, 3.8 µs for 50 parts, measured 2026-10-19) and treat every language alike, while tokens
penalise Turkish, which needs more tokens per character than English. The console's playground
already capped at 32 000 characters.

**Consequences.** A size in tokens can still be added for billing or provider limits later,
without changing the security limit. The engine's L0 scan has no limit of its own beyond this
one: it scans long text in overlapping chunks (`l0.max_chars` is a chunk size, not a cut-off).

