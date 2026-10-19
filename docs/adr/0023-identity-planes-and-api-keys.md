# ADR-0023 — Two identity planes: per-tenant API keys for workloads, people through the console

- **Status:** Accepted (product owner, 2026-10-18: platform foundation first). Implemented: `tenancy/`, `control_plane/keys.py`, contract `credentials` 0.1, evaluation API 0.6. §1 on-behalf-of assertion verified by the policy service since 2026-10-19 (`control_plane/actors.py`, credentials 0.2; console signing follows). Follow with the regional stack: §7 Redis buckets, `last_used_at`, `test`-key refusal on production bundles
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner
- **Builds on:** ADR-0016 (bearer token), ADR-0019 §1 (tenant from the credential), ADR-0021 (company → project → guardrail)

## Context

Almost all traffic is machine to machine: customer agents call `POST /v1/evaluate`. A smaller,
human flow edits and publishes policy through the console. Today both run on one shared
static bearer token per service (`api/routes.py: require_api_token`,
`control_plane/app.py: require_token`), `tenant_id` is a body field, and the console has no
login at all. The [threat model](../security/threat-model.md) ranks this R1/R2: any key holder
acts as any tenant, nothing can be revoked short of rotating everyone, and no change is
attributable to a person. Enterprise identity requirements (SSO, SCIM) concern only the human
plane; conflating the two planes is why "add SAML" is often mistaken for "fix tenancy".

## Decision

1. **Two planes, two credential types.**
   - **Workload plane:** per-tenant **API keys** authenticate agents and SDKs at the engine.
     OAuth 2.0 client credentials and mTLS are Enterprise additions later (same resolution
     path), not the first mechanism.
   - **Human plane:** people sign in to the console with OIDC (ADR-0024). Services never see a
     human's password or SSO token. The console calls the engine and the policy service with
     a **service credential** plus a short-lived, signed **on-behalf-of assertion** (JWT,
     ES256, `exp` ≤ 5 min; claims `sub`, `email`, `tenant_id`, `roles`, `session_id`). The
     policy service verifies the assertion against the console's public key and records the
     `sub` as the actor of every change (ADR-0030). A service credential alone cannot name an
     actor, so it cannot publish or activate.
2. **API key format and storage.** Secret: 32 random bytes, base62. Presentation:
   `umai_<env>_<region>_<key_id>_<secret>` with `env ∈ {live, test}`, `region ∈ {eu, us, sh}`
   (`sh` = self-hosted) and an 8-character `key_id` for lookup; the prefix lets secret
   scanners and the SDK's router recognise the key. Shown once at creation. Stored:
   `sha256(secret)` only — the secret has 256 bits of entropy, so a stolen hash is neither
   usable nor brute-forceable — plus `tenant_id`, optional `project_id`, `scopes`, `name`,
   `created_by`, `created_at`, optional `expires_at`, `revoked_at`, `last_used_at` (coarse:
   written at most once per minute). Comparison is constant-time on the hash.
3. **Scopes** limit what a key may call: `evaluate`, `bundles:read`, `usage:read`.
   `bundles:reload` is a control-plane scope that customer keys cannot hold. Default for a new
   key: `evaluate` only.
4. **Resolution on the request path keeps the engine a reader.** The control plane publishes
   a signed **key registry snapshot** per tenant (key ids, hashes, scopes, tenant, status) that
   engines load like bundles and entitlements (verify → validate → atomic swap → hot reload,
   ADR-0019 §3). No database call per request. Revocation and creation become effective
   within one poll interval (target ≤ 10 s in SaaS), which the console shows ("active in a
   few seconds"). A key with `env = test` is metered separately and never serves production
   traffic (the engine rejects it on a bundle marked `production`).
5. **Tenant binding.** The credential's tenant is authoritative. In the current contract
   `tenant_id` in the body must equal it, otherwise HTTP 403 `tenant_mismatch` (readiness
   test T1). Evaluation API 0.6 makes the body field optional and derived from the key.
6. **Rotation and revocation without downtime.** A tenant creates a second key, moves callers,
   revokes the first. The console lists `last_used_at` so an unused key can be revoked
   safely. Revoking is immediate in the control plane and effective at engines within the
   poll interval; every lifecycle step emits an audit event.
7. **Abuse limits per credential.** Admission applies a token-bucket rate limit per key and
   per tenant (ADR-0019 ingress note, Q27). Proposed defaults, to be confirmed by the product
   owner: Community 20 requests/s per key, Enterprise by contract; `429 rate_limited` with
   `Retry-After`, never a decision. Store: Azure Managed Redis per region in SaaS (clustered;
   keys hash-tagged per tenant, ADR-0031 §7), in-process for single-node self-hosted.
8. **Self-hosted** keeps the shared token of ADR-0016 as a valid credential for single-customer
   installs; per-tenant keys are available there too and recommended when more than one
   application calls the engine.
9. **Transport.** Credentials travel in the `Authorization: Bearer` header only, never in a
   query string; health routes stay unauthenticated; everything else on `/v1` requires a
   credential (unchanged).

## Alternatives considered

| Option | Why not |
|---|---|
| Keep one shared token and the body `tenant_id` | Any customer can act as any tenant; no revocation; no attribution. Disqualifying for SaaS (ADR-0019) |
| OAuth 2.0 client credentials as the only workload mechanism | Every SDK and extension needs a token endpoint round trip and refresh logic; adds a dependency on the identity service's availability to the request path. Offered later for Enterprise, not first |
| Self-contained JWT API keys (claims inside the key) | Cannot be revoked without a deny list, which is a registry anyway; longer keys |
| HMAC request signing (AWS SigV4 style) | Strong against replay, but SDK complexity and clock skew; not needed over TLS for this threat model. Possible Enterprise option |
| Hash keys with argon2/bcrypt | Designed for low-entropy passwords; needless cost per request for 256-bit random secrets |
| Engine queries the key table per request | Puts the control-plane database on the request path and breaks air-gapped installs; the snapshot pattern already exists for bundles |
| Trust an `X-Acting-User` header from the console | Any holder of the service credential could impersonate any user; a signed assertion costs one key pair |

## Consequences

- Contracts first (skill `change-contract`): evaluation API → 0.6 (`401`/`403` bodies
  `tenant_mismatch`, `insufficient_scope`, `rate_limited`; `tenant_id` optional); new
  contract `credentials` 0.1 (key record, key-registry snapshot, on-behalf-of assertion).
- Code: `tenancy/` (key parsing, registry snapshot source, admission dependency replacing
  `require_api_token`, rate limiter); control plane: key CRUD endpoints and audit events;
  console: key management page (create, show once, revoke, last used), assertion signing.
- Admission adds work on every request: measured and reported with `l0-latency` before the
  design is accepted (AGENTS rule 6; ADR-0019 Validation).
- Operations: console signing key pair and the control plane's snapshot signing key are
  held as described in ADR-0027.

## Validation

- Readiness suite: T1 (tenant mismatch → 403) turns from xfail to pass; new tests: a key of
  tenant A cannot evaluate against, list or reload tenant B's bundles; revoked key → 401
  within the poll interval; `evaluate`-only key gets 403 `insufficient_scope` on
  `GET /v1/bundles`; `test` key refused on a production bundle; rate limit → 429 with
  `Retry-After`; secrets never appear in logs or audit events (leak suite over audit payloads).
- Measured: admission p95 on the reference hardware; revocation latency end to end.
- Revisit if SDK telemetry shows customers need client-credentials flows from day one, or if
  revocation latency through snapshots proves unacceptable for an enterprise customer.
