# Contract: credentials

**Version:** 0.1 · **Producer → consumer:** control plane → engines (key-registry snapshot), control plane ↔ console (key records) · **Decision:** [ADR-0023](../adr/0023-identity-planes-and-api-keys.md)

Two shapes: the **API key record** the console manages, and the **key-registry snapshot** the
control plane publishes per tenant and every engine loads like a bundle (verify → validate →
atomic swap → hot reload). Secrets never appear in either: a record carries the SHA-256 of the
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
  "created_by": "control-token",
  "created_at": "2026-10-18T09:00:00Z",
  "expires_at": null,
  "revoked_at": null,
  "last_used_at": null,
  "status": "active"
}
```

`scopes` ⊆ `evaluate`, `bundles:read`, `usage:read` for customer keys (default `evaluate`);
`bundles:reload` is a control-plane scope that customer keys cannot hold. `status` is derived
(`revoked` when `revoked_at` is set). `last_used_at` is reserved: engines do not write to the
store, so it stays `null` until usage telemetry exists.

## Key-registry snapshot — `KeyRegistrySnapshot`

One file per tenant, `<key_registry_dir>/<tenant_id>.json`, written atomically by the control
plane; engines poll the directory (`UMAI_DE_KEY_REGISTRY_POLL_INTERVAL_S`, default 5 s).

```json
{
  "contract_version": "0.1",
  "tenant_id": "tenant-demo",
  "generated_at": "2026-10-18T09:00:05Z",
  "keys": [
    {
      "key_id": "Q7h2kP9c",
      "secret_sha256": "<64 hex>",
      "env": "live",
      "region": "eu",
      "scopes": ["evaluate"],
      "status": "active",
      "expires_at": null
    }
  ],
  "signing_key_id": "vm-2026-10",
  "signature": "<base64 Ed25519>"
}
```

| Field | Notes |
|---|---|
| `keys[].status` | `active` or `revoked`; revoked keys stay listed so a reload can never resurrect one |
| `signing_key_id`, `signature` | Ed25519 over the JSON of the snapshot without `signature`, with the control plane's bundle-signing key; verified against `UMAI_DE_BUNDLE_PUBLIC_KEYS`. Required whenever signed bundles are required (everything but development) |

Load rules (engine): an unparsable, unsigned-where-required or wrongly signed file keeps the
tenant's last good keys and is reported in the registry's `errors`; a duplicate `key_id`
across tenants is ignored and reported. Revocation and creation take effect within one poll
interval.

## Errors at admission (evaluation API 0.6)

| HTTP | `error` | When |
|---|---|---|
| 401 | `invalid_credential` | unknown key id, wrong secret, revoked, expired, wrong shared token; `WWW-Authenticate: Bearer` |
| 403 | `tenant_mismatch` | the body's `tenant_id` differs from the credential's tenant |
| 403 | `insufficient_scope` | the route's scope is not in the key's scopes |
| 429 | `rate_limited` | token bucket per credential exhausted; `Retry-After` in seconds |
| 400 | `tenant_required` | shared operator token without `tenant_id` in the body |

## Changelog

- 0.1 (2026-10-18): initial: key presentation, key record, per-tenant signed registry snapshot,
  admission errors (ADR-0023).
