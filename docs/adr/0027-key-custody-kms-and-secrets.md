# ADR-0027 — Key custody: signing keys in a KMS behind a `Signer` interface, per-tenant keys wrapped by KMS, secrets out of files

- **Status:** Proposed; amended by ADR-0031 (Azure): UMAI Cloud signs with ECDSA P-256 in Key Vault Premium, Ed25519 remains for self-hosted installs and existing bundles
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner
- **Builds on:** ADR-0005 (HMAC hashes), ADR-0012 (Ed25519-signed bundles), ADR-0019 (signed entitlements), ADR-0023 (key registry snapshots, on-behalf-of assertion), ADR-0025 (per-tenant envelope encryption)
- **Facts:** [`reference/key-management-services.md`](../reference/key-management-services.md), [`reference/azure-platform.md`](../reference/azure-platform.md) §1 (verified 2026-10-17)

## Context

Three kinds of keys exist or are planned, and today all of them live in files or environment
variables: the Ed25519 bundle-signing key (base64 in a file, loaded into the policy service's
memory for the process lifetime, printed to stdout by `cli keygen`), per-tenant HMAC keys
(`UMAI_DE_TENANT_HMAC_KEYS`, a JSON map in the environment), and the service tokens. Whoever
reads the host's environment or disk holds the integrity root of every engine and can
brute-force every tenant's entity hashes (threat model A4, A5, R6). SaaS also needs per-tenant
data keys for envelope encryption (ADR-0025) and a signing key for the console's on-behalf-of
assertions (ADR-0023). The reference check shows Ed25519 is now natively supported by AWS KMS
(FIPS 140-3 Level 3 HSMs, GA 2025-11-07) and by Vault Transit; by Google Cloud KMS only at
software protection level; by Azure Key Vault not at all. ADR-0031 fixed Azure as the
provider of UMAI Cloud, which is why the algorithm split in §1 and §2 exists.

## Decision

1. **Two algorithms, one registry.** Manifests carry an explicit `alg` field. Self-hosted and
   development signing and every existing bundle use `ed25519`; **UMAI Cloud signs with
   `ecdsa-p256` (`ES256`)** because no Azure key store holds Ed25519 (ADR-0031 §2). The
   verifier accepts only the algorithms the public-key registry names for a key id (never an
   algorithm taken from the artifact itself) and normalises ECDSA signatures to raw `r||s`.
2. **A `Signer` interface** — `sign(message) → (signature, key_id, alg)` — with three
   implementations: `kms_azure` (Key Vault Premium EC-HSM P-256, `ES256`; the application
   hashes with SHA-256 and sends the digest — Key Vault signs hashes only; FIPS 140-3 Level 3,
   non-exportable), `vault_transit` (key type `ed25519`, versioned signatures; self-hosted
   customers with Vault), and `local_file` (development; air-gapped self-hosted with the
   documented hardening: file mode 0600, dedicated service user). In SaaS the private key never
   enters application memory. Managed HSM is the drop-in for `kms_azure` if a customer requires
   a single-tenant HSM (ADR-0031 §2).
3. **Trust anchors and a public-key registry.** The release ships UMAI's root public key(s)
   (`trust_anchors` setting, replacing today's `bundle_public_keys`). The root signs a
   **public-key registry** (key id, `alg`, public key, purpose ∈ {bundles, entitlements,
   credentials, assertions}, `not_before`, `not_after`), delivered to engines as a signed
   snapshot like bundles. Artifact verification = look up the manifest's `key_id` in the
   registry, check validity window and purpose, verify locally. Historical keys stay in the
   registry so old bundles keep verifying; nothing at request time calls a provider's `Verify`.
   Self-hosted customers who run their own control plane install their own root (answers part
   of Q18: the customer's control plane signs with the customer's key; UMAI-signed templates
   are imported and re-signed).
4. **Rotation.** Signing keys rotate yearly or on suspicion: create the new key in the KMS, add
   it to the registry, switch the signer, keep the old key valid for verification until its
   `not_after`, then disable it in Key Vault. Key Vault rotates asymmetric keys by policy (a new
   version; old versions stay enabled for verification), and the runbook updates the registry. Vault's key versions map onto registry entries.
5. **Per-tenant HMAC keys** (ADR-0005) are generated at tenant onboarding (32 bytes, CSPRNG),
   stored **wrapped** by Key Vault Premium (`RSA-OAEP-256` with an RSA-HSM key; ADR-0031 §3)
   in the tenant record, and delivered to engines inside the per-tenant key snapshot, which
   the engine unwraps at load through its workload identity and keeps in memory only. Hash outputs carry a `key_version`; rotation
   happens only on compromise because it breaks correlation across time, by design.
   `UMAI_DE_TENANT_HMAC_KEYS` remains for development and single-tenant self-hosted installs.
6. **Per-tenant data keys** (ADR-0025) are generated in our code (CSPRNG) — Key Vault has no
   `GenerateDataKey` equivalent — used as AES-256-GCM keys, one per tenant with versions, and
   wrapped as in §5 with the versioned key id stored beside each wrapped key; the wrapping key
   rotates by Key Vault rotation policy and old versions keep unwrapping. Destroying a tenant's
   data keys is part of erasure.
7. **Service secrets** (database credentials, Redis, broker credentials) live in Key Vault
   secrets (Vault KV or a protected file on-prem), are read through Entra Workload ID on AKS,
   and never sit in `.env` files outside development. CI deploys use GitHub OIDC federation to
   an Entra application or user-assigned managed identity — no long-lived cloud keys in GitHub
   secrets (ADR-0031 §9). The on-behalf-of assertion (ADR-0023) is signed through the same
   `Signer`: `ES256` in UMAI Cloud, `EdDSA` (RFC 8037) self-hosted; verifiers accept both via
   the registry.
8. **`cli keygen`** writes the private key to a file with mode 0600 (or straight into Vault)
   and prints only the public key and key id; `--stdout` is an explicit flag with a warning.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep key files and environment JSON | A host compromise is a silent integrity and confidentiality compromise; fails every SOC 2 key-management expectation |
| Keep Ed25519 in UMAI Cloud through HCP Vault Dedicated on Azure | Adds a vendor and sub-processor; absent from Sweden Central and Germany West Central; HashiCorp lists Ed25519 in Transit as not FIPS-compliant (ADR-0031) |
| Azure Managed HSM for everything | ≈ $2.3k per month per region for a single-tenant HSM nobody has required; Key Vault Premium EC-HSM keys are already FIPS 140-3 Level 3 |
| Per-tenant bundle-signing keys | The manifest already names the owning tenant; the signer is the control plane (UMAI's or the customer's), not the tenant |
| Fetch HMAC keys from the KMS per request | Puts a network call on the L0 path; the snapshot + in-memory cache keeps the engine's latency budget |

## Consequences

- Contracts: manifest `alg` and `key_id` semantics (policy-bundle), new `key-registry` 0.1,
  entitlement and credential snapshots signed the same way.
- Code: `crypto/signer.py` with the three backends; verification through the registry;
  `trust_anchors` setting; tenant key snapshot; `cli keygen` change.
- Operations: Key Vault Premium per region with private endpoints and RBAC roles (IaC); a
  rotation runbook; Key Vault diagnostic logs of sign and unwrap operations in Log Analytics
  become evidence.
- Cost is negligible at our volumes (Key Vault Premium: $5 per EC-HSM key per month, $0.15 per
  10k advanced operations, $1 per scheduled rotation; signatures happen at publish time, not
  per request).

## Validation

- No private key material on application hosts: a test deployment grep over environment,
  mounted files and process memory dumps finds none (operational check, recorded).
- Rotation drill: publish with key A, rotate to key B, verify that bundles signed with A still
  load and new bundles carry `key_id = B`; disable A after `not_after`.
- Registry test: a manifest with an unknown `key_id`, a wrong `purpose`, an expired key or an
  algorithm outside the allowlist is refused at load.
- Leak suite extended with key-material patterns (base64 Ed25519 private keys, Vault tokens).
- KMS audit log shows every signature attributable to the publishing principal.
- Signature-encoding test for `kms_azure`: raw `r||s` and DER inputs both verify (ADR-0031).
- Revisit if Azure adds Ed25519 to Key Vault (SaaS could return to one algorithm) or if a
  customer requires a single-tenant HSM (Managed HSM behind the same interface).
