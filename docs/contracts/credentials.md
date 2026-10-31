# Contract: credentials

**Version:** 0.9 · **Producer → consumer:** control plane → engines (key-registry snapshot), control plane ↔ console (key records), console → policy service (on-behalf-of assertion) · **Decisions:** [ADR-0023](../adr/0023-identity-planes-and-api-keys.md), [ADR-0029](../adr/0029-authorization-roles-and-four-eyes.md)

Three shapes: the **API key record** the console manages, the **key-registry snapshot** the
control plane publishes per tenant and every engine loads like a bundle (verify → validate →
atomic swap → hot reload), and the **on-behalf-of assertion** the console sends with every call
it makes for a person. Secrets never appear in them: a record carries the SHA-256 of the
secret, which is shown exactly once, in the response that created it.

## API key presentation

```text
umai_<env>_<region>_<key_id>_<secret>
umai_live_eu_Q7h2kP9c_<43 base62 characters>
```

| Part | Values | Notes |
|---|---|---|
| `env` | `live`, `test` | `test` keys are metered separately; refusal on production bundles is planned |
| `region` | `eu`, `us`, `sh` | the region the key was minted in; `sh` = self-hosted |
| `key_id` | 8 base62 characters | lookup id; appears in logs and audit events |
| `secret` | 32 random bytes, base62 | 256 bits of entropy; stored as `sha256(secret)` only |

Sent as `Authorization: Bearer <key>` only; never in a query string.

## Key record — `ApiKeyRecord` (control-plane API)

```json
{
  "key_id": "Q7h2kP9c",
  "tenant_id": "tenant-demo",
  "name": "sdk-prod",
  "env": "live",
  "region": "eu",
  "scopes": ["evaluate"],
  "application_id": null,
  "allowed_ips": ["203.0.113.0/24", "2001:db8:1::/48"],
  "created_by": "control-token",
  "created_at": "2026-10-18T09:00:00Z",
  "expires_at": null,
  "revoked_at": null,
  "last_used_at": null,
  "status": "active"
}
```

`scopes` ⊆ `evaluate`, `gateway`, `bundles:read`, `usage:read` for customer keys (default `evaluate`;
`gateway` admits the key at the UMAI gateway, [gateway-api](gateway-api.md));
`bundles:reload` is a control-plane scope that customer keys cannot hold. `application_id` (0.6,
ADR-0039; `gateway_id` in 0.5) says which application a key belongs to. The key then works on
every connection of that application: on the gateway it selects the application and refuses any
other ([gateway-api](gateway-api.md)), on the evaluation API the application's guardrail applies
and a request naming another is refused ([evaluation-api](evaluation-api.md) 0.10). It is set
when the key is created (`POST /v1/tenants/{id}/keys` with `{"name", "application_id"}`; the
application must exist; `scopes` then default to `["evaluate", "gateway"]`) and cannot change;
create a new key to move an application. `null`: the key belongs to no application.
`allowed_ips` (0.8, ADR-0046) are the networks the key may be used from (below); `null` or
absent: any address. `status` is derived
(`revoked` when `revoked_at` is set). `last_used_at` is reserved: engines do not write to the
store, so it stays `null` until usage telemetry exists.

### Allowed networks (0.8, ADR-0046)

A key may be restricted to 1–50 IPv4 or IPv6 networks. Each entry is a CIDR (`203.0.113.0/24`,
`2001:db8:1::/48`) or a bare address (one host: `/32`, `/128`); the policy service stores them
canonically (lower-case, compressed IPv6, a bare address with its prefix length). Refused with
**422** `{"error": "invalid_allowed_ips", "detail": "<which entry, why>"}`: anything that is not
an address or network, host bits set (`203.0.113.5/24` → "entry 1 has host bits set: write
203.0.113.0/24 for the whole network, or the address alone for one host"), a zone index
(`fe80::1%eth0`), a repeated network, more than 50 entries.

| Route | Permission | Does |
|---|---|---|
| `POST /v1/tenants/{id}/keys` with `"allowed_ips": [...]` | `key.create` | Creates the key with its networks |
| `PATCH /v1/tenants/{id}/keys/{key_id}` with `{"allowed_ips": [...]}` | `key.update` (owner, admin, operator) | Replaces an active key's networks; `null` or `[]` clears them (any address). Answers `{"key": <record>, "published": <bool>}`. 404 unknown key; 409 `key_revoked` for a revoked key; 422 `invalid_allowed_ips`, or an unknown field |

Every change republishes the tenant's snapshot; engines and the gateway enforce it within one
poll interval. A request with the key from another address gets **403** `ip_not_allowed`
before anything is evaluated or forwarded (below).

## Key-registry snapshot — `KeyRegistrySnapshot`

One file per tenant, `<key_registry_dir>/<tenant_id>.json`, written atomically by the control
plane; engines poll the directory (`UMAI_DE_KEY_REGISTRY_POLL_INTERVAL_S`, default 5 s).

```json
{
  "contract_version": "0.5",
  "tenant_id": "tenant-demo",
  "generated_at": "2026-10-18T09:00:05Z",
  "entitlements": {
    "edition": "community",
    "monthly_requests": 10000,
    "monthly_test_requests": 1000,
    "max_input_chars": 32000
  },
  "keys": [
    {
      "key_id": "Q7h2kP9c",
      "secret_sha256": "<64 hex>",
      "env": "live",
      "region": "eu",
      "scopes": ["evaluate", "gateway"],
      "application_id": "hr-assistant",
      "status": "active",
      "expires_at": null,
      "allowed_ips": ["203.0.113.0/24", "2001:db8:1::/48"]
    }
  ],
  "signing_key_id": "vm-2026-10",
  "signature": "<base64 Ed25519>"
}
```

| Field | Notes |
|---|---|
| `contract_version` | `0.5` since credentials 0.8 (entries may carry `allowed_ips`); `0.4` since 0.7 (`entitlements`); `0.3` since 0.6 (entries may carry `application_id`); engines still accept `0.1` to `0.4` files (0.2 calls the field `gateway_id`; its signature covers that name). An engine older than 0.8 refuses a 0.5 file and keeps the tenant's last good keys: update engines first |
| `entitlements` | The tenant's limits ([entitlements](entitlements.md) 0.1, ADR-0043): edition, `monthly_requests`, `monthly_test_requests`, `max_input_chars`; null or absent fields mean no limit (the size: the deployment's). Absent when the deployment has no plans file |
| `keys[].application_id` | The key's application (0.6); absent when the key belongs to none |
| `keys[].allowed_ips` | The key's networks, canonical CIDRs, 1–50 (0.8, ADR-0046); absent: any address |
| `keys[].status` | `active` or `revoked`; revoked keys stay listed so a reload can never resurrect one |
| `signing_key_id`, `signature` | Ed25519 over the JSON of the snapshot without `signature`, with the control plane's bundle-signing key; verified against `UMAI_DE_BUNDLE_PUBLIC_KEYS`. Required whenever signed bundles are required (everything but development) |

Load rules (engine): an unparsable, unsigned-where-required or wrongly signed file keeps the
tenant's last good keys and is reported in the registry's `errors`; a duplicate `key_id`
across tenants is ignored and reported. Revocation and creation take effect within one poll
interval; so does a change of the tenant's limits.

## On-behalf-of assertion — `ActorAssertion` (0.2)

The console signs one for every policy-service call it makes for a person and sends it as
`X-Umai-Actor: <compact JWS>` next to its service credential (`Authorization: Bearer <control
token>`); the assertion never replaces that credential. The policy service verifies it, takes the
person, the tenant and the roles from it, authorizes the route with the permission matrix
(ADR-0029 §3) and records `sub` as the actor of every change. No human token reaches a service
(ADR-0023 §1).

JWS header (exact): `{"alg": "ES256", "typ": "umai-actor+jwt", "kid": "<key id>"}`. Claims:

```json
{
  "iss": "umai-console",
  "aud": "umai-policy-service",
  "sub": "auth0|6512f0c0a1b2c3d4e5f60718",
  "email": "ada@example.com",
  "email_verified": true,
  "name": "Ada Example",
  "tenant_id": "tenant-demo",
  "session_id": "q3Jt0yY3m7xQ2bQe6l1oZp8m0WcH4n5sV9aK2dF7gR1",
  "iat": 1792390000,
  "exp": 1792390120,
  "jti": "a3f1c9e2"
}
```

| Rule | |
|---|---|
| Algorithm, type | ES256 (P-256) and `typ` `umai-actor+jwt` only; anything else is refused, never downgraded |
| Keys | `UMAI_DE_ACTOR_PUBLIC_KEYS` = `{"<kid>": "<base64 SubjectPublicKeyInfo DER>"}`; the private key stays with the console (Key Vault in SaaS, ADR-0027) |
| Issuer, audience | `UMAI_DE_ACTOR_ISSUER` (default `umai-console`), `UMAI_DE_ACTOR_AUDIENCE` (default `umai-policy-service`) |
| Lifetime | `exp - iat` 1–300 s; 30 s clock skew either way; `jti` unique per assertion (not stored: the lifetime bounds replay) |
| Tenant, roles | `tenant_id` absent before the person chose an organization. The person's roles are read from the tenant store (ADR-0033 §1); a `roles` claim, if sent, is ignored (0.3) |
| Person | `email`, `email_verified` and `name` come from the console's ID token; organizations need `email_verified` true, and the store keeps the person's e-mail and name up to date from them |
| Session (0.8) | `session_id` is the console session's id ([console-sessions](console-sessions.md) 0.1: 256 random bits, base64url). Every route except starting the session refuses an ended session, and in `UMAI_DE_CONSOLE_SESSIONS=required` (default in production) one without a record: 401 `session_ended` |
| Mode | `UMAI_DE_ACTOR_ASSERTION`: `required` (default in production) — tenant actions need an assertion, the control token alone runs operator actions only (tenants, API keys, reads; drafts, draft tests, publishing, activating and an application's guardrail need a person); `optional` (default elsewhere) — without an assertion the control token acts as the operator for every action |

A key pair for development: `openssl ecparam -name prime256v1 -genkey -noout -out actor.pem`;
the public value for the setting: `openssl ec -in actor.pem -pubout -outform DER | base64 -w0`.

Errors (`{"detail": {"error": "<code>", "detail": "<text>"}}`): 401 `invalid_actor` (malformed,
wrong algorithm or type, unknown key, bad signature, expired, wrong issuer or audience); 401
`session_ended` (the assertion's console session ended or is unknown, 0.8); 403
`actor_required` (required mode, tenant action without an assertion); 403 `tenant_required` (the
assertion names no tenant); 403 `tenant_mismatch` (the target tenant is not the assertion's); 403
`forbidden` (the roles do not allow the action).

## Client address (0.8, ADR-0046)

Admission needs the caller's address for allowed networks and for the failed-credential
throttle. It is the socket peer, unless the peer is a trusted proxy
(`UMAI_DE_TRUSTED_PROXIES`, a JSON list of CIDRs; **default empty: no proxy is trusted**). Behind
a trusted proxy `X-Forwarded-For` (every line, in order) is read from the right, skipping
trusted hops; the first untrusted hop is the client, so entries a caller wrote themselves are
never used. No other header (`Forwarded`, `X-Real-IP`) is read; uvicorn's own proxy-header
handling is off in the images (`--no-proxy-headers`). An IPv4-mapped IPv6 address counts as
IPv4. A client whose address cannot be read is outside every allowed network. The single VM
trusts its Caddy only (`deploy/vm`).

## Errors at admission (evaluation API 0.6, 0.14)

Checked in this order; a refused request is never evaluated or forwarded.

| HTTP | `error` | When |
|---|---|---|
| 401 | `invalid_credential` | missing credential, unknown key id, wrong secret, revoked, expired, wrong shared token; `WWW-Authenticate: Bearer`. A presented credential that fails counts against its source |
| 429 | `too_many_failed_attempts` | (0.8) the source (an IPv4 address or an IPv6 /64) presented `UMAI_DE_AUTH_FAILURE_LIMIT` bad credentials (default 20) within `UMAI_DE_AUTH_FAILURE_WINDOW_S` (default 60 s); every credential from it is refused before any lookup until a token is back (`Retry-After` in seconds). A success does not reset the count |
| 403 | `ip_not_allowed` | (0.8) the key has `allowed_ips` and the client address is outside them (or unknown); not counted as a failed credential, does not spend the key's rate budget |
| 429 | `rate_limited` | token bucket per credential exhausted; `Retry-After` in seconds |
| 403 | `tenant_mismatch` | the body's `tenant_id` differs from the credential's tenant |
| 403 | `insufficient_scope` | the route's scope is not in the key's scopes |
| 400 | `tenant_required` | shared operator token without `tenant_id` in the body |

## Changelog

- 0.9 (2026-10-30, ADR-0046): `allowed_ips` on key records, key creation and registry entries
  (snapshot payload `contract_version` 0.5; 0.1 to 0.4 still read); `PATCH
  /v1/tenants/{id}/keys/{key_id}` (permission `key.update`) and 422 `invalid_allowed_ips`, 409
  `key_revoked`; admission errors 403 `ip_not_allowed` and 429 `too_many_failed_attempts`; the
  client address from trusted proxies only. Tenant-store migration 0013.
- 0.8 (2026-10-30, ADR-0024 amendment): the assertion's `session_id` names a console session
  ([console-sessions](console-sessions.md) 0.1); the policy service refuses an ended one (401
  `session_ended`). Claims, key records and snapshots unchanged.
- 0.7 (2026-10-30, ADR-0043): the snapshot carries the tenant's `entitlements` (contract
  [entitlements](entitlements.md) 0.1); snapshot payload `contract_version` 0.4 (0.1 to 0.3
  still read).
- 0.6 (2026-10-20): `application_id` replaces `gateway_id` (ADR-0039): a key belongs to an
  application and works on all its connections (scopes default to `evaluate` and `gateway`);
  snapshot payload `contract_version` 0.3 (0.1 and 0.2 still read); tenant-store migration 0004.
- 0.5 (2026-10-20): `gateway_id` binds a key to one gateway (ADR-0038): on key records, on
  key creation and on registry entries; snapshot payload `contract_version` 0.2 (0.1 still read).
- 0.4 (2026-10-19): scope `gateway` for the UMAI gateway (ADR-0034); snapshot payload unchanged.
- 0.3 (2026-10-19): assertion claims `email_verified` and `name`; roles are read from the
  tenant store and the `roles` claim is ignored (ADR-0033 §1).
- 0.2 (2026-10-19): on-behalf-of assertion (ES256 JWS from the console to the policy service,
  ADR-0023 §1) and its errors. Key records and snapshots unchanged (snapshot payloads keep
  `contract_version` 0.1).
- 0.1 (2026-10-18): initial: key presentation, key record, per-tenant signed registry snapshot,
  admission errors (ADR-0023).
