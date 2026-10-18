# Azure platform for UMAI Cloud: keys, Postgres, regions, Redis, containers, logging, ingress, e-mail

**Last verified:** 2026-10-17 (§1–§9), **2026-10-18** (§10 Communication Services Email) · **Used by:** sign-in and onboarding design (e-mail sender, §10), launch plan P1/P2 (cloud and region choice), ADR-0025
(Postgres/RLS, envelope encryption), ADR-0026 (region pinning, same-jurisdiction DR), ADR-0027
(key custody, `Signer`), ADR-0028 (Redis for quotas), ADR-0030 (audit export, telemetry) ·
Labels: see [README](README.md). Facts are **vendor claims** from Microsoft Learn, azure.microsoft.com
and the Azure Retail Prices API (`prices.azure.com`, USD, consumption, list price) unless marked otherwise.

The product owner requires **Azure** for UMAI Cloud (multi-tenant SaaS, EU first, US later, DR in
the same jurisdiction). The launch plan and ADRs were written against AWS. This document records
the Azure equivalents and the constraints that change the design. The general KMS comparison stays in
[key-management-services.md](key-management-services.md).

## 1. Key Vault and Managed HSM

### Key types and operations

| Item | Key Vault Standard | Key Vault Premium | Managed HSM |
|---|---|---|---|
| Tenancy | Multi-tenant | Multi-tenant, HSM-backed keys | **Single-tenant** HSM cluster with its own security domain |
| EC signing | EC P-256/P-256K/P-384/P-521, `ES256`/`ES256K`/`ES384`/`ES512` (software keys) | Same curves as EC-HSM keys | Same curves (all keys HSM) |
| **Ed25519 / OKP** | **No** | **No** | **No** (key-type tables list only EC, RSA, oct; re-checked 2026-10-17) |
| RSA | 2048/3072/4096: sign `PS*`/`RS*`; wrap/encrypt `RSA-OAEP-256` (recommended), `RSA-OAEP` and `RSA1_5` (not recommended) | Same, HSM-protected | Same |
| Symmetric (AES, `oct-HSM`) | No | 128/192/256: `AES-KW`, `AES-GCM`, `AES-CBC`, HMAC `HS256/384/512` — **public preview, no SLA, not for production** | 128/192/256: `AES-KW`, `AES-GCM`, `AES-CBC`, `HS256/384/512` — documented without a preview label |
| FIPS level | Software keys: FIPS 140-2 Level 1 (`hsmPlatform=0`) | New keys on `hsmPlatform=2`: **FIPS 140-3 Level 3**; older keys on platform 1: FIPS 140-2 Level 2 (keys can't migrate between platforms) | **FIPS 140-3 Level 3** (Marvell LiquidSecurity) |
| Export | No export of private or symmetric material; BACKUP/RESTORE only within the service | Same | Same; Secure Key Release to attested TEEs for exportable keys |
| Data residency | — | — | "Doesn't store/process customer data outside the region" of the instance |
| Access control | Azure RBAC (Key Vault Crypto Officer / Crypto User / Crypto Service Encryption User) | Same | Local data-plane RBAC (Managed HSM Crypto User/Officer, per-key scope) separate from Azure RBAC |
| Private networking | Private endpoints | Private endpoints | Private endpoints |

**Sign is "sign hash".** The service does not hash; the application hashes locally and sends the
digest, and Microsoft recommends verifying locally with the public key. Python
(`azure-keyvault-keys` 4.11.3, released 2026-10-02):

```python
from azure.keyvault.keys.crypto import CryptographyClient, SignatureAlgorithm

digest = hashlib.sha256(manifest_bytes).digest()  # computed client-side
result = CryptographyClient(key_id, credential).sign(SignatureAlgorithm.es256, digest)
# result.signature; wrap_key(KeyWrapAlgorithm, key) / unwrap_key(KeyWrapAlgorithm, encrypted_key)
```

Signature encoding: `ES256` is the JOSE algorithm from RFC 7518, which defines a raw 64-byte
`r||s` signature, not DER. That the SDK returns exactly this form is **unverified**; test it and
normalise in the verifier.

### Envelope encryption

- **Key Vault Premium (GA path):** generate the AES-256 data key locally, wrap it with an RSA-HSM
  key (`RSA-OAEP-256`), and store the wrapped key. Wrapping can be done locally with the public key;
  unwrapping always calls the service. AES-KW on Premium is preview and not for production.
- **Managed HSM:** an AES-256 `oct-HSM` key-encryption key with `AES-KW` or `AES-GCM`.
- There is no `GenerateDataKey` equivalent in the documented operations, so data keys come from the
  application's CSPRNG (`cryptography`). Unlike AWS KMS, that is our code, not a service call.

### Rotation

- Key Vault: per-key **rotation policy** (rotate after creation or before expiry; minimum interval
  7 days; Event Grid "near expiry" notification). Rotation creates a new version and **old versions
  stay enabled**, so old signatures still verify and old DEKs still unwrap. Use versionless URIs
  for the current key, but store the versioned key id alongside wrapped data and signatures.
- Managed HSM: its key reference says it supports automated rotation policies and on-demand
  rotation. The Key Vault page says "Rotate … not on Managed HSM". The two pages contradict each
  other; the Managed HSM page is newer and more specific (**treat Managed HSM rotation as
  likely, verify in a test instance**).
- Expired or not-yet-valid keys still work for `verify`, `unwrap` and `decrypt` but not for `sign`
  or `wrap`, which suits the "old key verifies, new key signs" model in ADR-0027 §4.

### Prices (Retail Prices API, West Europe; identical in North Europe, Sweden Central, Germany West Central)

| Meter | Price |
|---|---|
| Standard/Premium operations (RSA 2048, secrets) | $0.03 per 10k |
| Advanced key operations (ECC, RSA 3072/4096) | $0.15 per 10k |
| Premium HSM-protected RSA 2048 key | $1 per key/month |
| Premium HSM-protected advanced key (EC, RSA 3k/4k) | $5 per key/month for the first 250 keys, then $2.50 / $0.90 / $0.40 at higher tiers |
| Automated key rotation | $1 per scheduled rotation |
| Managed HSM pool (Standard B1) | **$3.20 per hour** (≈ $2,336 per month at 730 h, single instance) |

Whether Managed HSM also charges per key or per operation is **unverified**: the API returned no such
meter.

### HashiCorp Vault for Ed25519 on Azure

- **HCP Vault Dedicated** runs in these Azure regions: **West Europe, North Europe, France Central,
  UK South**, plus West US 2, East US, East US 2, Central US and others. **Sweden Central and
  Germany West Central are not listed** (developer.hashicorp.com, last updated 2026-02-13).
- Transit supports `ed25519`, but the Vault FIPS documentation lists Ed25519 in Transit as
  **not FIPS-compliant** (see [key-management-services.md](key-management-services.md)). Vault is
  also a separate vendor and sub-processor, with its own residency and DPA, and adds operational
  surface. Which HCP tier includes Transit, and the current price, are **unverified**.

## 2. Azure Database for PostgreSQL Flexible Server

| Topic | Fact |
|---|---|
| Versions | **18** (18.6), 17 (17.11), 16, 15, 14; 13/12/11 in extended support. PG18 limitations: some extensions missing, no `io_method = io_uring` |
| Superuser | Not available. Server admin is `NOSUPERUSER, CREATEDB, CREATEROLE`, member of `azure_pg_admin` ("pseudo-superuser"); `azuresu` (real superuser) is Microsoft-only |
| Row-level security | Standard PostgreSQL RLS (`ENABLE ROW LEVEL SECURITY`, policies, default-deny when enabled with no policy). New roles default to `NOBYPASSRLS` |
| BYPASSRLS | PG ≥ 16: standard PG16 behaviour; `azure_pg_admin` can create roles with `BYPASSRLS`. PG ≤ 15: `azure_pg_admin` can do admin tasks needing BYPASSRLS but can't grant it to non-admin roles. `azure_pg_admin` itself is a restricted role you can't alter |
| Public schema | Owned by `azure_pg_admin` on all versions (the PG15 `pg_database_owner` change doesn't apply). Run `REVOKE CREATE ON SCHEMA public FROM PUBLIC` or use our own schema |
| PITR | Retention **7 days by default, up to 35**; RPO up to about 5 min (WAL archiving); restore always creates a **new server in the same region** |
| Long-term retention | Azure Backup, up to 10 years: logical `pg_dump` backups, weekly/monthly/yearly schedules, max 1 TiB, restore as files only |
| Geo-redundant backup | Set **only at server creation**. Copies go **only to the Azure paired region**, with RPO up to 1 h; **no PITR from the geo copy** (latest available backup only). Primary must be in a paired region |
| Cross-region read replicas | Up to 5 per primary (cascading to 30); asynchronous physical replication; **any region where the service exists** (not limited to pairs); promote for DR; virtual endpoints keep a stable hostname. General Purpose or Memory Optimized only (not Burstable) |
| CMK (customer-managed key) | Key Vault or Managed HSM in the **same region**. **RSA only** (2048/3072/4096), user-assigned managed identity, soft delete plus purge protection. Set at creation only and can't be reverted. Geo-backup needs a **second key in a vault in the geo region** with a separate identity. Versionless key URI allows automatic version pickup; if the key is lost, the server goes **Inaccessible** within about 60 min |
| Encryption and transport | AES-256 at rest (FIPS 140-2 validated module), TLS 1.2+ enforced; private access by VNet integration (no public endpoint) |

**Geo-backup targets for EU regions** (pairs from the region-pairs page; geo-backup support from the
PostgreSQL region table):

| Primary | Paired region (geo-backup target) | Same jurisdiction? | Notes |
|---|---|---|---|
| West Europe (Netherlands) | **North Europe (Ireland)** | EU → EU, EUDB | Both unrestricted; both list geo-redundant backup ✅ |
| North Europe (Ireland) | West Europe (Netherlands) | EU → EU | Same |
| Germany West Central (Frankfurt) | Germany North (Berlin) | DE → DE | Germany North is **access-restricted** (support request); PG table lists it with geo-backup ✅ |
| France Central (Paris) | France South (Marseille) | FR → FR | France South is **access-restricted** |
| Sweden Central (Gävle) | Sweden South | SE → SE | Sweden South is **access-restricted** and **absent from the PostgreSQL region table**. Sweden Central shows geo-backup ✅, but where the copy lands and whether it needs access approval is **unverified** |

Zone-redundant HA: the PostgreSQL region table marks **West Europe, North Europe, Germany West Central,
East US, East US 2 and West US 2 with `$`**. The legend defines `$` twice, once as "new zone-redundant
HA deployments temporarily blocked" and once as "new server deployments temporarily blocked".
**Sweden Central and France Central have zone-redundant HA without `$`.** Check this at provisioning
time; it can change week to week (**the meaning of the ambiguous legend is unverified**).

## 3. Regions

| Region | Location | AZs | Pair | PG geo-backup | Managed Redis | Sentinel SIEM | HCP Vault Dedicated |
|---|---|---|---|---|---|---|---|
| West Europe | Netherlands | 3 | North Europe | ✅ | ✅ (priced) | ✅ (+ data lake) | ✅ |
| North Europe | Ireland | 3 | West Europe | ✅ | ✅ | ✅ (+ data lake) | ✅ |
| Germany West Central | Frankfurt | 3 | Germany North (restricted) | ✅ | ✅ | ✅ | ✗ |
| Sweden Central | Gävle | 3 | Sweden South (restricted) | ✅ (target unverified) | ✅ | ✅ | ✗ |
| France Central | Paris | 3 | France South (restricted) | ✅ | ✅ | ✅ (+ data lake) | ✅ |
| East US 2 | Virginia | 4 (one zone in preview) | Central US | ✅ | ✅ | ✅ (+ data lake) | ✅ |
| Central US | Iowa | 3 | East US 2 | ✅ | ✅ | ✅ (+ data lake) | ✅ |

Managed HSM pool and Key Vault Premium HSM keys are priced in all four EU regions checked (WE, NE,
SWC, GWC). Managed Redis is priced in WE, NE, SWC, GWC, FRC, EUS2 and CUS. **A price row is evidence,
not a guarantee of capacity**; confirm SKU availability in the subscription.

**Paired regions are not automatic DR.** Microsoft states that pairs give staggered updates, a
recovery priority and (mostly) in-geography residency, but no automatic failover. A few services
(GRS storage, PG geo-backup) replicate only to the pair. Nonpaired EU regions (Italy North, Poland
Central, Spain Central, Austria East, Belgium Central, Denmark East) depend on service-level
geo-replication such as PG read replicas.

**EU Data Boundary (EUDB).** Covers the EU and EFTA (incl. Norway, Switzerland). For Azure, *regional*
services deployed in an EU/EFTA region are in scope; non-regional services need per-service
configuration. Azure Resource Manager can be pinned to the EUDB only on a **new tenant with no
subscriptions**, and that choice can't be undone (it then blocks deployments outside EUDB regions).
Log Analytics stores data in its region. Sentinel processes data in Europe for European workspaces.
**Explicitly excluded:** **Azure Front Door and Azure CDN** ("not all traffic will stay within the
EU Data Boundary"; anycast to global PoPs), and **Defender for Cloud** (limited pseudonymized/customer
data transfers to the US unless the tenant is provisioned in the EU).

## 4. Redis

| | Azure Cache for Redis (Basic/Standard/Premium) | Azure Cache for Redis Enterprise / Enterprise Flash | **Azure Managed Redis** |
|---|---|---|---|
| Status | **Retires 2028-09-30**, disabled from 2028-10-01 | **Retires 2027-03-31**, disabled from 2027-04-01 | Current offering; Microsoft recommends migrating now |
| Engine | Fork of Redis OSS | Redis Enterprise | Redis Enterprise software, **Redis 7.4.x** |
| Topology | — | — | **Clustered by default** (OSS cluster policy); non-clustered option up to 25 GB; cross-slot commands need hash-tagged keys |
| HA | — | — | Primary/replica shards across ≥ 2 nodes, **zone-redundant by default** in AZ regions; HA can be disabled (dev/test only) |
| Auth and network | — | — | Microsoft Entra ID auth, Private Link, persistence, active geo-replication (not B0/B1) |
| Price (West Europe) | — | — | Balanced B0 $0.019/h, B1 $0.038/h, B5 $0.186/h; Compute Optimized X3 $0.261/h |

Suitability for rate limits and quotas (ADR-0028): token buckets and counters with Lua or
`MULTI` work on a clustered instance as long as each script touches keys in one hash slot. Use a
per-tenant hash tag such as `{tenant}:rl:key`. **Do not enable active geo-replication across
jurisdictions** (ADR-0026); per-region instances only.

## 5. Containers

| | AKS | Azure Container Apps |
|---|---|---|
| Model | Full Kubernetes API; AKS Standard (you operate) or **AKS Automatic** (Microsoft manages nodes, scaling, security defaults, upgrades; workload identity and OIDC issuer preconfigured) | Serverless containers on Kubernetes + KEDA + Dapr + Envoy; **no Kubernetes API access** |
| Fit for UMAI | Engine, policy service, console (identity is Auth0, ADR-0024 §4); Kubernetes NetworkPolicy for "regional stacks can't reach each other" (S1-3); Gatekeeper/Ratify admission | Simpler for engine/console; isolation via environments and VNet rather than Kubernetes NetworkPolicy (**details unverified**); Key Vault secret references built in |
| Price (West Europe) | Standard tier "Uptime SLA" $0.10/h per cluster; Automatic hosted control plane $0.16/h plus per-vCPU-hour compute surcharges | Not checked (**unverified**) |
| Identity | **Microsoft Entra Workload ID**: projected service-account token with OIDC federation; `azure-identity` ≥ 1.13.0 (`DefaultAzureCredential`/`WorkloadIdentityCredential`); max **20 federated credentials per managed identity**; pod label `azure.workload.identity/use: "true"` required | System- or user-assigned managed identity |

**Container Registry signing and scanning:**
- ACR stores **Notary Project (Notation)** signatures as OCI referrers. Notation signs with a Key
  Vault certificate via the `notation-azure-kv` plugin, or with Microsoft "Artifact Signing"
  (short-lived certificates). ACR supports the OCI Referrers API **except on registries encrypted
  with CMK**.
- **cosign:** Microsoft Learn does not document cosign for ACR. Sigstore's registry-support list
  names ACR (**secondary source**). The launch plan's "signed images (cosign)" works for storage,
  but Azure's admission tooling verifies **Notation only**.
- **AKS Image Integrity** (Ratify + Azure Policy + Gatekeeper) is still **preview**: "shouldn't
  be used for production", **Notation is the only verifier, Audit is the only effect**. Enforcing
  (deny) needs self-managed open-source Ratify + Gatekeeper.
- **Defender for Containers** scans ACR images with Microsoft Defender Vulnerability Management
  (OS packages on Linux and Windows, language packages on Linux). New pushes are scanned within
  hours; a daily rescan covers images pushed or pulled in the last 30 days or running in monitored
  clusters. Defender for Cloud is excluded from the EUDB (§3).

## 6. Secrets

- **AKS:** apps read Key Vault secrets with Workload ID and `azure-keyvault-secrets` (Key Vault
  Secrets User role). This is the equivalent of IRSA plus Secrets Manager.
- **Container Apps:** a secret can reference `keyVaultUrl` + managed identity. A versionless URI
  picks up new versions **within 30 minutes** and restarts revisions that use the secret in env vars.
  `listSecrets` is granted by wildcard roles such as **Container Apps Operator**; use custom roles.
- **CI:** GitHub OIDC federation to an Entra app or user-assigned managed identity (same 20-credential
  limit) replaces long-lived cloud keys (ADR-0027 §7).

## 7. Logging, SIEM, object storage

- **Log Analytics** stores data in the workspace region and is in the EUDB. **Data export rules** go
  continuously to a Storage account or Event Hubs in the **same region** as the workspace, can target
  **immutable** storage (enable protected append writes), and drop data after 12 h of retries if the
  destination is unavailable.
- **Microsoft Sentinel** stores data in the workspace region and processes it in Europe for European
  workspaces. SIEM is supported in all candidate EU regions (§3).
- **Event Hubs:** streaming target for customer SIEMs (Kafka-compatible, **unverified**). Basic and
  Standard tiers allow 10 event hubs per namespace.
- **Blob immutable storage (WORM):** time-based retention (1 day to 146,000 days) and legal hold, at
  container level or version level (version level needs versioning). Policies must be **locked** to
  meet SEC 17a-4(f), CFTC 1.31(c)-(d) and FINRA 4511 (Cohasset assessment in the Service Trust
  Portal). A locked policy can be extended but not shortened (max 5 extensions at container
  level). No extra capacity charge. Incompatible with blob point-in-time restore. All redundancy
  options are supported. This is the S3 Object Lock equivalent for audit exports (ADR-0030 §5).

## 8. Ingress

| | Azure Front Door Standard/Premium | Application Gateway v2 (Standard_v2 / WAF_v2) |
|---|---|---|
| Scope | **Global, non-regional**; anycast to Microsoft PoPs; **excluded from the EU Data Boundary** | **Regional**, deployed in our VNet |
| TLS | TLS 1.2 and 1.3 only (1.0/1.1 unsupported); predefined or custom TLS policy; DHE ciphers retired 2026-04-01; no client mTLS | TLS 1.0/1.1 support ended 2025-08-31; **`AppGwSslPolicy20220101S`** (min 1.2, enables 1.3, GCM-only ECDHE) or CustomV2; default for API ≥ 2023-02-01 is `AppGwSslPolicy20220101` |
| HSTS | Add the response header with a rules-engine action (**unverified**: not checked on Learn) | Rewrite rule adding `Strict-Transport-Security` (documented how-to, v2 only); HTTP→HTTPS redirect rule |
| WAF | Premium: managed rule sets + bot protection (**not re-verified here**) | WAF_v2 with OWASP CRS / DRS (**not re-verified here**) |
| Private origins | Premium: Private Link to origins | Native: private backend in the VNet |

**Implication:** Front Door terminates TLS at global edge PoPs, so prompts would be decrypted outside
the region. That is the "content crosses borders at the ingress" failure ADR-0026 rejects. Use
**Application Gateway WAF_v2 per region** for `eu.api.` and `eu.console.`. Front Door is acceptable
only for content-free global surfaces (marketing site, status page, global directory sign-in front
door, if it carries no tenant content).

## 9. Compliance reports

Azure, Dynamics 365, Power Platform and select Microsoft 365 services have **SOC 2 Type 2** reports
(security, availability, processing integrity, confidentiality; combined with CSA STAR and BSI
C5:2020). They cover a rolling 12-month window, with new reports semi-annually (periods end 31 Mar and
30 Sep) and **quarterly bridge letters**. Download from the **Service Trust Portal** (SOC reports
section; Microsoft account required). ISO 27001 and other reports are on the same portal
(**not individually re-checked**).

## 10. Communication Services Email (verified 2026-10-18)

Checked because the product owner chose Azure Communication Services (ACS) for the e-mails of
the sign-in and onboarding design (Q38, answered as A13). Vendor claims from Microsoft Learn, the Azure retail
price API and Auth0's docs; labels in brackets resolve to the source list at the end of this
section.

**Retirement (decisive).** On 2026-09-23 Microsoft announced the retirement of ACS as a
standalone offering. **ACS Email is "Retired": not available after 2028-09-30.** From
**2026-10-23** Microsoft begins limiting onboarding for customers without an ACS resource created
before that date (first measure named: phone numbers), and "additional measures" may follow at any
time. New ACS Email resources can still be created, "subject to change at any time"; Microsoft
recommends migrating off rather than onboarding new solutions. Verified domains keep working until
2028-09-30; quota increases are handled case by case; only break-fix support. Microsoft's
suggested alternatives: Microsoft 365 High Volume Email (internal recipients only), Exchange
Online, or marketplace providers (Infobip, Telesign; Blip in the summary table). The page was last
updated 2026-10-08 [acs-retire].

| Topic | Fact | Source |
|---|---|---|
| Resources | Communication Services resource plus a separate Email Communication Services resource; domains (customer-managed or Azure-managed, location `Global`) under the Email resource, connected to the Communication resource; both resources must share the same data location; up to 100 linked domains | [acs-email], [acs-domains], [acs-connect], [acs-arm] |
| Data location | Chosen at creation (Europe, Germany, France, Norway, Switzerland, UK, United States and others). For Email: content processed in real time in the data location, MailFrom values stored there, hard-bounced addresses kept temporarily, delivery logs in your Azure Monitor workspace; "data may transit or be processed in other geographies". The Email quickstart still says to select United States (a preview note); Microsoft's automation sample uses Europe: confirm in the portal | [acs-privacy], [acs-create], [acs-sample] |
| EU Data Boundary | Europe or an EU country keeps data in the EU Data Boundary apart from listed residual transfers (for ACS only PSTN emergency callback numbers); no Email-specific statement. Exceptions that touch Email: the Event Grid system topic is global; the managed suppression list is global | [acs-eudb], [acs-eudb-transfers], [acs-privacy], [acs-suppress] |
| Custom domain DNS | Verification TXT generated by the portal; SPF `v=spf1 include:spf.protection.outlook.com -all`; DKIM CNAMEs `selector1-azurecomm-prod-net._domainkey` and `selector2-…` pointing to `…._domainkey.azurecomm.net`; for a subdomain in the parent zone the names gain the subdomain (`…._domainkey.notify`); DMARC guidance `p=quarantine` or `p=reject` with `rua`; an MX record is recommended for reputation; subdomains are used in Microsoft's examples | [acs-domains], [acs-dmarc], [acs-quota] |
| Senders | Default MailFrom `donotreply@<domain>`; up to 100 sender usernames per domain, **but not on custom domains with the default limits** (a quota increase comes first) | [acs-senders] |
| Limits | Custom domain: **30 e-mails per minute, 100 per hour** per subscription by default; higher limits by support request after a 2–4-week ramp-up with low failure rates (up to 72 h, not automatic). Azure-managed domains (`*.azurecomm.net`): testing only, 5 per minute, 10 per hour | [acs-limits], [acs-quota], [acs-managed] |
| Authentication | Access key / connection string or Microsoft Entra ID (recommended; managed identity with a role allowing `CommunicationServices` read/write); SMTP `smtp.azurecomm.net:587` STARTTLS with an Entra application. **Auth0's `azure_cs` provider takes only a connection string** and a plain From address; Auth0's IP addresses must be allowed | [acs-auth], [acs-mi], [acs-smtp], [auth0-acs], [auth0-email-api] |
| Price | $0.00025 per e-mail (per recipient) and $0.00012 per MB, USD, same in West Europe and Global | [acs-price], [acs-pricing] |
| Python SDK | `azure-communication-email` 1.1.0 (2025-10-17); async client `azure.communication.email.aio.EmailClient` with `begin_send` (long-running operation ending `Succeeded` or `Failed`); delivery outcome only through Event Grid (`EmailDeliveryReportReceived`) or logs | [acs-pypi], [acs-py], [acs-py-aio], [acs-rest], [acs-events] |
| Engagement tracking | Off by default, per domain; cannot be enabled on default-limit or Azure-managed domains | [acs-tracking] |
| Logs | Only through a diagnostic setting; status logs contain the recipient address | [acs-logs] |
| Compliance | ACS in Azure's SOC 2 / ISO 27001 scope and explicit DPA coverage: *unverified* (lists in the Service Trust Portal); body retention not documented beyond real-time processing | [acs-audit], [acs-privacy] |
| Auth0 plan | Auth0's docs do not say which plans may use the provider; the pricing page lists "Email Workflow" only from Essentials (`identity-providers.md`) | [auth0-acs] |

**What this means for us.** ACS works today for beta volumes (30 per minute, 100 per hour),
processes in the EU with Europe as data location, and Auth0 supports it natively. But it is in its
retirement window: choosing it means a temporary sender with a fixed end date (2028-09-30) and a
risk that new onboarding closes earlier. If ACS is kept, its resources should exist before
2026-10-23, and the sender stays behind an adapter (Auth0's provider is a configuration value; our
own e-mails go through one `EmailSender` interface), so a later move is configuration.
**Decision (A13, product owner, 2026-10-18):** ACS as a temporary sender, resources created before
2026-10-23, a long-term provider chosen before the retirement.

[acs-retire]: https://learn.microsoft.com/en-us/azure/communication-services/acs-retirement-and-breaking-changes-guide
[acs-email]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/email-overview
[acs-domains]: https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/add-custom-verified-domains
[acs-connect]: https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/connect-email-communication-resource
[acs-arm]: https://learn.microsoft.com/en-us/azure/templates/microsoft.communication/emailservices
[acs-privacy]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/privacy
[acs-create]: https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/create-email-communication-resource
[acs-sample]: https://learn.microsoft.com/en-us/azure/communication-services/samples/email-resource-management
[acs-eudb]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/european-union-data-boundary
[acs-eudb-transfers]: https://learn.microsoft.com/en-us/privacy/eudb/eu-data-boundary-ongoing-partial-transfers
[acs-suppress]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/sender-reputation-managed-suppression-list
[acs-dmarc]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/email-authentication-best-practice
[acs-quota]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/email-quota-increase
[acs-senders]: https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/add-multiple-senders
[acs-limits]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/service-limits
[acs-managed]: https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/add-azure-managed-domains
[acs-auth]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/authentication
[acs-mi]: https://learn.microsoft.com/en-us/samples/azure-samples/communication-services-dotnet-quickstarts/email-sample-send-email-with-managed-identity/
[acs-smtp]: https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/send-email-smtp/smtp-authentication
[auth0-acs]: https://auth0.com/docs/customize/email/smtp-email-providers/azure-communication-services
[auth0-email-api]: https://auth0.com/docs/api/management/v2/emails/post-provider
[acs-price]: https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Email%27
[acs-pricing]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/email-pricing
[acs-pypi]: https://pypi.org/project/azure-communication-email/
[acs-py]: https://learn.microsoft.com/en-us/python/api/overview/azure/communication-email-readme
[acs-py-aio]: https://learn.microsoft.com/en-us/python/api/azure-communication-email/azure.communication.email.aio.emailclient
[acs-rest]: https://learn.microsoft.com/en-us/rest/api/communication/email/email/send?view=rest-communication-email-2025-09-01
[acs-events]: https://learn.microsoft.com/en-us/azure/event-grid/communication-services-email-events
[acs-tracking]: https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/enable-user-engagement-tracking
[acs-logs]: https://learn.microsoft.com/en-us/azure/communication-services/concepts/analytics/logs/email-logs
[acs-audit]: https://learn.microsoft.com/en-us/azure/compliance/offerings/cloud-services-in-audit-scope

## What this means for us

1. **AWS → Azure mapping** for the launch plan and ADRs:

   | AWS item (where used) | Azure equivalent | Change in design |
   |---|---|---|
   | KMS Ed25519 `ECC_NIST_EDWARDS25519` (ADR-0027 §2) | **None at HSM level.** Key Vault Premium EC-HSM P-256 `ES256` (FIPS 140-3 L3), or Managed HSM | Switch signing to **ECDSA P-256** (see 2) |
   | KMS `GenerateDataKey` + symmetric CMK (ADR-0025 §5, ADR-0027 §5–6) | App-generated AES-256 DEK, wrapped by Key Vault Premium RSA-HSM (`RSA-OAEP-256`) or Managed HSM AES (`AES-KW`) | DEK generation moves into our code; versioned key id stored with each wrapped DEK |
   | KMS automatic symmetric rotation | Key Vault rotation policy (asymmetric too), $1 per rotation | Old versions stay enabled; registry design unchanged |
   | CloudTrail of sign/decrypt (ADR-0027 Ops) | Key Vault / Managed HSM diagnostic logs → Log Analytics | Same evidence role |
   | Secrets Manager + IAM roles / IRSA (ADR-0027 §7, S0-8) | Key Vault secrets + Entra Workload ID (AKS) or managed identity (Container Apps) | 20 federated credentials per identity |
   | GitHub OIDC → AWS role | GitHub OIDC → Entra app / user-assigned managed identity | — |
   | RDS/Aurora Postgres, PITR, DR replica (ADR-0025 Ops) | PostgreSQL Flexible Server 17/18, PITR ≤ 35 days, geo-backup to the pair or cross-region read replica | Application role must not be in `azure_pg_admin`; geo-backup only at creation and no PITR from the geo copy |
   | Frankfurt / Ireland / Stockholm (launch plan P1) | West Europe / North Europe / Sweden Central / Germany West Central / France Central | See 3 |
   | ElastiCache Redis (ADR-0028 Ops) | **Azure Managed Redis** (not Azure Cache for Redis, which is retiring) | Clustered by default; hash-tag rate-limit keys |
   | EKS/ECS (launch plan S0-8) | AKS (Automatic or Standard); Container Apps possible for stateless parts | NetworkPolicy needs AKS |
   | ECR + cosign (launch plan S0-7) | ACR + **Notation** (Key Vault certificate); cosign only as a secondary option | Admission verification is Notation-only, and the managed add-on is preview/audit-only |
   | Image scanning (ECR/Inspector) | Defender for Containers (MDVM) + Trivy in CI | Defender for Cloud is outside the EUDB |
   | CloudFront/ALB + AWS WAF, TLS 1.2+/HSTS (launch plan S0-8) | **Application Gateway WAF_v2** per region, `AppGwSslPolicy20220101S`, HSTS via rewrite | Front Door only for content-free global surfaces |
   | PrivateLink / VPC endpoints | Private endpoints (Key Vault, Managed HSM, Redis), PG VNet integration | — |
   | CloudWatch Logs / metrics (launch plan S0-9) | Azure Monitor + Log Analytics (regional) | — |
   | S3-compatible SIEM export bucket (ADR-0030 §5) | Blob Storage (S3 API not native: write via Blob API) + Event Hubs for streaming | Exporter needs a Blob target, not just S3 (**S3-compatible Blob access unverified**) |
   | S3 Object Lock (audit WORM) | Blob immutable storage, locked time-based policy | — |
   | AWS SaaS Lens (ADR-0025 reference) | Azure Architecture Center multitenant guidance (**not reviewed here**) | Reference only |
   | AWS Artifact SOC 2 | Service Trust Portal SOC 2 Type 2 + bridge letters | — |

2. **Ed25519 vs P-256: recommend ECDSA P-256 (`ES256`) in Key Vault Premium** for bundle manifests,
   entitlement records and assertions. No Azure key store holds Ed25519. The only Azure-hosted
   Ed25519 option is HCP Vault Dedicated, which adds a vendor and sub-processor, is not in Sweden
   Central or Germany West Central, and whose Ed25519 is explicitly not FIPS-compliant. Key Vault
   Premium P-256 keys are FIPS 140-3 Level 3, cost $5 per key per month plus $0.15 per 10k
   signatures, and rotate by policy. Cost of the switch: manifest `alg` = `ES256`, the verifier
   accepts `Ed25519` (local and self-hosted keys) and `ES256` side by side through the key registry,
   the hash is computed client-side, and signature encoding is normalised (`r||s` vs DER). Self-hosted
   and air-gapped deployments can keep Ed25519 local keys because the `alg` lives in the registry,
   not in the artifact. Managed HSM (≈ $2.3k per month per region) is only justified if a customer
   requires a single-tenant HSM.
3. **Region recommendation (proposal, needs ADR-0026 update):** **EU primary West Europe, DR North
   Europe.** It is the only EU pair where both sides are unrestricted, both are in the EU and EUDB,
   PG geo-backup lands in the other without an access request, and Managed HSM, Managed Redis,
   Sentinel (with data lake) and HCP Vault are all present. The risk is the `$` mark on
   zone-redundant HA for new PG servers in both regions. If it still applies at provisioning, the
   alternative is **Sweden Central primary** (zone-redundant HA available) with DR as a **PG
   cross-region read replica in North Europe or West Europe**, still EU, not dependent on the
   restricted Sweden South. Germany West Central and France Central work only with an access
   request for their restricted pairs, or with read-replica DR. **US later: East US 2 ↔ Central
   US** (paired, both unrestricted, geo-backup ✅).
4. **Postgres RLS works as designed** (ADR-0025). Add three checks to the role test: the application
   role is not a member of `azure_pg_admin`, it is `NOBYPASSRLS`, and it owns no tenant tables.
   `FORCE ROW LEVEL SECURITY` stays mandatory. Use PG 17 or 18. Decide CMK and geo-backup **at
   creation**: neither can be added later.
5. **Ingress and the EUDB:** do not put Front Door in front of `*.api` or `*.console`; it is outside
   the EU Data Boundary by design. Consider pinning a **new Entra tenant** to the EUDB before the
   first subscription exists. It is irreversible and blocks US-region deployments from that tenant,
   so the US stack would need its own tenant (decision for ADR-0026).
6. **Supply chain:** move the launch plan from cosign to Notation with a Key Vault certificate, or
   keep cosign in CI and accept that cluster-side verification is not managed. Plan self-managed
   Ratify + Gatekeeper with a deny effect if admission enforcement is a launch control.
7. **Follow-ups:** ADR-0027 (algorithm, provider mapping), ADR-0026 (regions, EUDB tenant), the
   launch plan P1/P2 and S0-7/S0-8, and `key-management-services.md` consequence 1 all need
   updating. That is a decision, so write an ADR (skill `write-adr`).

## Sources (accessed 2026-10-17)

Key Vault: learn.microsoft.com/azure/key-vault/keys/about-keys-details (updated 2026-10-06) ·
/key-vault/managed-hsm/about-keys-details (2026-05-21) · /key-vault/managed-hsm/overview (2026-10-09) ·
/key-vault/keys/how-to-configure-key-rotation · learn.microsoft.com/python/api/azure-keyvault-keys/azure.keyvault.keys.crypto.cryptographyclient ·
pypi.org/project/azure-keyvault-keys · prices.azure.com (serviceName `Key Vault`, regions westeurope,
northeurope, swedencentral, germanywestcentral).
Vault: developer.hashicorp.com/hcp/docs/hcp/supported-env/azure (updated 2026-02-13).
PostgreSQL: learn.microsoft.com/azure/postgresql/configure-maintain/concepts-supported-versions (2026-08-26) ·
/postgresql/security/security-manage-database-users · /postgresql/security/security-access-control (2026-07-14) ·
/postgresql/backup-restore/concepts-backup-restore (2026-07-05) · /postgresql/backup-restore/how-to-restore-paired-region ·
/postgresql/read-replica/concepts-read-replicas · /postgresql/security/security-data-encryption (2026-09-18) ·
/postgresql/overview (regions table, updated 2026-09-05).
Regions and EUDB: learn.microsoft.com/azure/reliability/regions-paired (updated 2026-10-15) ·
/azure/reliability/regions-list (2026-10-13) · learn.microsoft.com/privacy/eudb/eu-data-boundary-learn ·
/privacy/eudb/eu-data-boundary-configure-azure-nonregional-services (2026-10-16) ·
/privacy/eudb/eu-data-boundary-excluded-services (2026-04-13) · /privacy/eudb/eu-data-boundary-ongoing-partial-transfers.
Redis: learn.microsoft.com/azure/azure-cache-for-redis/retirement-faq · /azure/redis/overview ·
prices.azure.com (productName contains `Managed Redis`).
Containers: learn.microsoft.com/azure/container-apps/compare-options · /container-apps/manage-secrets ·
/aks/workload-identity-overview · /aks/image-integrity · /container-registry/overview-sign-verify-artifacts ·
/container-registry/container-registry-tutorial-sign-build-push · /defender-for-cloud/agentless-vulnerability-assessment-azure ·
prices.azure.com (serviceName `Azure Kubernetes Service`) · docs.sigstore.dev/cosign/system_config/registry_support (secondary).
Logging and storage: learn.microsoft.com/azure/azure-monitor/logs/logs-data-export ·
/azure/sentinel/geographical-availability-data-residency · /azure/storage/blobs/immutable-storage-overview.
Ingress: learn.microsoft.com/azure/frontdoor/end-to-end-tls · /application-gateway/application-gateway-ssl-policy-overview ·
/application-gateway/hsts-http-headers-portal (search result only, not opened).
Compliance: learn.microsoft.com/azure/compliance/offerings/offering-soc-2 · servicetrust.microsoft.com/viewpage/SOC.
