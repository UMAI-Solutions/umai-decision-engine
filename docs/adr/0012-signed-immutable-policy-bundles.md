# ADR-0012 — Policies are signed, immutable bundles; the engine only reads them

- **Status:** Accepted
- **Date:** 2026-10-16
- **Deciders:** product owner (UMAI)

## Context

The engine needs policy configuration on every request, must keep working when whatever
manages policies is down, and must run on single-server on-prem installations without
internet. Policies will be edited in a dashboard and published live (ADR-0013).

## Decision

- A **policy bundle** is an immutable, versioned artifact: `bundle.yaml` + `manifest.json`
  (+ optional `tests/`). A published `(bundle_id, version)` never changes; a change is a new
  version.
- The manifest carries the SHA-256 of `bundle.yaml` and an **Ed25519 signature**. Engines
  hold only public keys: they verify but cannot sign. (`umai-engine` used a shared-secret
  HMAC; asymmetric keys stop a modified on-prem server from forging bundles.)
- Which version is live is a separate **active pointer** per bundle id. Activating or
  rolling back = moving the pointer; no bundle content changes.
- The engine **reads** bundles through a `BundleSource` interface and never edits, stores
  drafts or authors policy:
  - `directory` (R&D and air-gapped on-prem): `policies/<bundle_id>/<version>/…` and
    `policies/<bundle_id>/active`;
  - `http_pull` (later): ETag polling plus an optional "new version" push signal.
- On load: verify hash and signature → validate schema and semantics → compile → **atomic
  swap**. A bundle that fails any step is rejected, the previous one stays active, and the
  error is reported. A last-known-good copy is cached on disk, so a restart does not depend
  on the source being reachable.
- Requests reference a bundle by id and either an explicit version or the active pointer.
  Each decision records the bundle id, version and content hash.
- Unsigned bundles are accepted only when `settings.require_signed_bundles` is false
  (development default).

## Alternatives considered

| Option | Why not |
|---|---|
| Fetch policy from a remote service per request | Latency and availability on every request |
| Policy stored in an engine database | Makes the runtime stateful; drafts and approvals are not runtime concerns |
| Redis as source of truth (as `umai-engine`) | Not an audit trail; acceptable only as a cache |
| HMAC signatures | Every verifier can also sign |

## Consequences

- One artifact format for cloud, on-prem and tests; the policy dashboard (ADR-0013) only
  has to produce it.
- Key management: signing keys live with whoever publishes (per tenant on-prem), public keys
  in engine settings.

## Validation

Tests: tampered bundle rejected; bad signature rejected; invalid new version keeps the old
one active; pointer change takes effect without restart; restart works from the disk cache.
