# Key management services for Ed25519 signing and envelope encryption

**Last verified:** 2026-10-17 · **Used by:** ADR-0027 (key custody), ADR-0025 (per-tenant data keys) ·
Labels: see [README](README.md). **Vendor claims** from official documentation and pricing pages
unless marked otherwise.

The engine signs bundle manifests with **Ed25519** (`policy/manifest.py`, ADR-0012). The
question for SaaS: which KMS/HSM can hold that key so the private part never enters application
memory, and what changes if we switched to ECDSA P-256.

## Ed25519 support

| Provider | Ed25519 native | Other signing algorithms | Exportable? FIPS | Price | Python | Asymmetric key rotation |
|---|---|---|---|---|---|---|
| **AWS KMS** | **Yes** — GA 2025-11-07, all Regions. Key spec `ECC_NIST_EDWARDS25519`; algorithms `ED25519_SHA_512` (`MessageType=RAW`) and `ED25519_PH_SHA_512` | ECDSA P-256/384/521, secp256k1, RSA 2048–4096, ML-DSA 44/65/87, SM2 (China) | Private key cannot leave the HSM (only `GetPublicKey`); **FIPS 140-3 Level 3** HSMs | $1/key/month; ECC sign $0.15 per 10k requests (Ed25519 row not shown separately — *unverified*, assumed ECC rate); asymmetric ops outside the free tier | `boto3` (`kms.sign(..., SigningAlgorithm="ED25519_SHA_512")`) | Automatic rotation is **symmetric only**; asymmetric = create a new key and repoint the alias; the old key still verifies until disabled |
| **Google Cloud KMS** | **Software protection level only**: `EC_SIGN_ED25519` (PureEdDSA). **Not** available at HSM / HSM single-tenant / EXTERNAL levels | `EC_SIGN_P256_SHA256` (recommended), P-384, secp256k1 (HSM), RSA PSS/PKCS1, ML-DSA / SLH-DSA (software) | No export operation. Cloud HSM: FIPS 140-2 Level 3; software level: FIPS 140-2 Level 1 → **Ed25519 at GCP is Level 1 only** | Software key version $0.06/month, ops $0.03 per 10k; HSM EC key $2.50/month (first 2,000), ops $0.15 per 10k; single-tenant HSM $3,500/month | `google-cloud-kms` | No automatic rotation for asymmetric keys; create a new version; old versions stay active for verification |
| **Azure Key Vault / Managed HSM** | **No** OKP/Ed25519 key type (docs 2026-10-06 / 2026-05-21) | EC P-256/P-256K/P-384/P-521 (`ES256`…), RSA 2–4k (`PS*`, `RS*`); service signs a digest you compute | No export; Key Vault Premium `hsmPlatform=2` and Managed HSM: FIPS 140-3 Level 3; software keys Level 1 | Ops $0.03 per 10k (RSA 2048), advanced (ECC) $0.15 per 10k; Premium HSM ECC key $5/key/month (first 250); Managed HSM pool ≈ $3.20/hour | `azure-keyvault-keys` | **Automatic rotation policy** for RSA/EC keys; old versions stay enabled and may verify |
| **HashiCorp Vault Transit / HCP Vault Dedicated** | **Yes**: key type `ed25519` (sign, verify, derive); prehashed and context variants (Enterprise) | `ecdsa-p256/384/521`, `rsa-2048/3072/4096`; Enterprise: ML-DSA, SLH-DSA, hybrid | `exportable` is a creation-time flag, default false, cannot be turned off once on. Vault Enterprise has FIPS 140-3 builds, **but the docs list Ed25519 in Transit as not FIPS-compliant** | Self-hosted OSS free; HCP Vault Dedicated hourly tiers plus per-client fees (indicative list via IBM) | `hvac` | `rotate` creates a new version; signatures carry `vault:vN:`; `min_decryption_version` bounds which versions still verify; `auto_rotate_period` available |
| **AWS CloudHSM** (optional) | Yes via PKCS#11 `CKM_CLOUDHSM_EDDSA` — **only on hsm2m.medium in non-FIPS mode** | RSA, ECDSA, ML-DSA, HMAC | Single-tenant cluster; FIPS 140-3 in FIPS mode | ≈ $1.45/HSM/hour; two HSMs recommended for HA | PKCS#11 client (`python-pkcs11`), you operate it | No versioning; you manage key objects |

## Consequences for the design (ADR-0027)

**Decision update 2026-10-17 (ADR-0031):** UMAI Cloud runs on Azure, so SaaS signing uses ECDSA
P-256 in Key Vault Premium; Ed25519 remains for self-hosted installs, Vault Transit and every
existing bundle. Azure specifics: [azure-platform.md](azure-platform.md) §1. The provider-neutral
analysis below is kept for reference.

1. **Ed25519 can stay** if the cloud is AWS (KMS, FIPS 140-3 L3) or the signer is Vault
   Transit (self-hosted; FIPS compliance not claimed for Ed25519). On GCP it exists only at
   software level (Level 1); on Azure not at all.
2. **Switching to ECDSA P-256 (SHA-256)** makes every provider work at HSM level and keeps
   FIPS claims for the signing operation. Cost: the verifier must accept two algorithms during
   migration (manifest `alg` + key id); signature encoding differs (DER vs raw `r||s`) and must
   be normalised in code.
3. **Rotation is our design regardless of provider**: only Azure and Vault rotate asymmetric
   keys automatically; AWS and GCP need a new key/version and a repoint. Keep every historical
   public key (key id, algorithm, `not_after`) in our own registry so old bundles verify, and
   never depend on a provider's `Verify` call at request time (the engine verifies locally with
   the public key, as today).
4. **Self-hosted parity**: a `Signer` interface — `sign(message) → (signature, key_id, alg)` —
   with implementations for AWS KMS, Vault Transit and a local key file (development, air-gapped).
5. **Envelope encryption** for per-tenant data keys (ADR-0025) uses the same provider's
   symmetric key (`GenerateDataKey` / Vault `datakey`); symmetric keys rotate automatically on
   AWS KMS and Vault.

## Sources (accessed 2026-10-17)

AWS KMS: docs.aws.amazon.com/kms/latest/developerguide/asymmetric-key-specs.html ·
aws.amazon.com/about-aws/whats-new/2025/11/aws-kms-edwards-curve-digital-signature-algorithm/ ·
docs.aws.amazon.com/kms/latest/developerguide/rotate-keys.html · aws.amazon.com/kms/faqs/ ·
aws.amazon.com/kms/pricing/. CloudHSM: docs.aws.amazon.com/cloudhsm/latest/userguide/pkcs11-mechanisms.html ·
/hsm-types.html · aws.amazon.com/cloudhsm/pricing/.
GCP: docs.cloud.google.com/kms/docs/algorithms · cloud.google.com/kms/pricing ·
docs.cloud.google.com/kms/docs/key-rotation · docs.cloud.google.com/kms/docs/hsm ·
cloud.google.com/blog/products/identity-security/new-google-cloud-whitepaper-cloud-key-management-service/.
Azure: learn.microsoft.com/azure/key-vault/keys/about-keys-details ·
/managed-hsm/about-keys-details · /managed-hsm/overview · /keys/how-to-configure-key-rotation ·
prices.azure.com (Retail Prices API, westeurope).
Vault: developer.hashicorp.com/vault/api-docs/secret/transit · /vault/docs/secrets/transit ·
/vault/docs/enterprise/fips · /vault/docs/enterprise/fips/fips1403 · /hcp/docs/vault/tiers-and-features ·
ibm.com/products/hashicorp/pricing.
SDKs: pypi.org/project/boto3 · google-cloud-kms · azure-keyvault-keys · hvac.
