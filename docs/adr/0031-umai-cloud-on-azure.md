# ADR-0031 — UMAI Cloud on Azure: regions, keys, data services, ingress and supply chain

- **Status:** Proposed (the provider is a product-owner constraint; the consequences below await acceptance)
- **Date:** 2026-10-17
- **Deciders:** product owner (provider); engineering (consequences)
- **Amends:** ADR-0012 (signature algorithm), ADR-0024 §4, ADR-0025 §1/§4/§9, ADR-0026 §1/§6, ADR-0027 §1–§2/§5–§7, ADR-0028 §3, ADR-0030 §5–§6
- **Answers:** launch-plan P1 (Q28)
- **Facts:** [`reference/azure-platform.md`](../reference/azure-platform.md); Entra section of [`reference/identity-providers.md`](../reference/identity-providers.md); provider-neutral comparison in [`reference/key-management-services.md`](../reference/key-management-services.md). All verified 2026-10-17; every "unverified" label there still applies here.

## Context

The product owner requires UMAI Cloud to run on **Azure** (EU first, US later). The readiness
plan and ADR-0023 to ADR-0030 were written against AWS assumptions: KMS with native Ed25519,
`GenerateDataKey`, Secrets Manager, EKS, cosign, an S3 bucket for exports. Three Azure facts change
the design rather than the vocabulary: **no Azure key store supports Ed25519**; **Azure Front Door
is excluded from the EU Data Boundary**; PostgreSQL Flexible Server's **geo-redundant backup is
pair-bound and set only at creation**. Everything else is a mapping. Self-hosted installs are not
affected by this ADR.

## Decision

1. **Provider and regions.** UMAI Cloud runs on Azure. EU stack: primary **West Europe**
   (Netherlands), disaster recovery **North Europe** (Ireland) — the only EU pair where both
   regions are unrestricted, both are inside the EU Data Boundary (EUDB) and Postgres geo-backup
   lands in the other without an access request. **Fallback** if zone-redundant HA for new
   Postgres servers is still blocked in both at provisioning time (the `$` flag in the Postgres
   region table, meaning *unverified*): primary **Sweden Central** with a cross-region read replica
   in North Europe as the DR path. US stack later: **East US 2 ↔ Central US**. The outcome of the
   HA check is recorded in the S0 readiness review.
2. **Signing algorithm: ECDSA P-256 in UMAI Cloud.** Bundle, entitlement, key-registry and
   credential manifests published by UMAI Cloud are signed with **`ES256`** (ECDSA P-256,
   SHA-256) using an EC-HSM key in **Key Vault Premium** (FIPS 140-3 Level 3, non-exportable,
   ≈ $5 per key per month plus $0.15 per 10k operations). The application computes the SHA-256
   digest locally ("sign hash"), the verifier accepts the raw `r||s` encoding and normalises
   DER if the SDK returns it (encoding *unverified*: tested before S0). **Ed25519 stays valid**
   for self-hosted and development signing (local key or Vault Transit) and for every existing
   bundle. This amends ADR-0012: the algorithm is no longer fixed to Ed25519 but named per key
   in the public-key registry (`alg ∈ {ed25519, ecdsa-p256}`, ADR-0027 §3); the manifest `alg`
   field moves from "reserved" to **S0 work**. Managed HSM (≈ $2.3k per month per region) is
   used only if a customer requires a single-tenant HSM.
3. **Envelope encryption and database encryption.** Key Vault has no `GenerateDataKey`: tenant
   data keys and HMAC keys are generated in our code (CSPRNG, 32 bytes) and **wrapped with an
   RSA-HSM key in Key Vault Premium using `RSA-OAEP-256`** (AES-KW there is preview); the
   versioned key id is stored with each wrapped key. Unwrapping always calls the service;
   wrapping may use the public key locally. PostgreSQL Flexible Server runs with a
   **customer-managed key** (RSA in Key Vault, same region, user-assigned identity, soft delete
   and purge protection) and a second key in the DR region for geo-backup — both set at creation,
   because neither can be added later.
4. **PostgreSQL.** Azure Database for PostgreSQL Flexible Server, version 17 or 18, General
   Purpose or Memory Optimized (read replicas need it). PITR retention set to 35 days,
   geo-redundant backup enabled at creation (restores the latest geo copy only, no PITR from
   it). The application role is **not** a member of `azure_pg_admin` (Flexible Server's
   pseudo-superuser group, which can create `BYPASSRLS` roles on PG ≥ 16), is `NOBYPASSRLS`, owns
   no tenant table (ADR-0025). `REVOKE CREATE ON SCHEMA public FROM PUBLIC`; our own schema.
5. **Ingress is regional.** Content hostnames (`eu.api.`, `eu.console.`, later `us.`) sit
   behind a regional **Application Gateway WAF_v2** with `AppGwSslPolicy20220101S` (TLS 1.2
   minimum, 1.3 enabled) and HSTS added by a rewrite rule. **Azure Front Door and Azure CDN never
   front content hostnames**: they terminate TLS at global points of presence outside the EUDB.
   Front Door may front content-free global surfaces only (marketing site, status page, the
   sign-in front door if it carries no tenant content). Internal traffic uses private endpoints
   (Key Vault, Redis, Storage) and VNet integration (Postgres).
6. **Compute and supply chain.** **AKS** (Automatic or Standard) runs the engine, policy service
   and console per region (identity is Auth0, §10); Kubernetes NetworkPolicy backs the isolation tests. Images
   live in **ACR** and are signed with **Notation** using a Key Vault certificate (admission
   verification on AKS is Notation-only and still preview/audit-only; enforcement needs
   self-managed Ratify + Gatekeeper). cosign signatures are added only if the product owner wants
   customer-side verification of self-hosted images with Sigstore tooling (launch-plan P11).
   Scanning: Trivy in CI; Defender for Containers in the registry is optional because Defender
   for Cloud is outside the EUDB (P11).
7. **Redis.** **Azure Managed Redis** per region (zone-redundant, clustered by default; Azure
   Cache for Redis retires 2028-09-30). Rate-limit and quota keys are hash-tagged per tenant
   (`{tenant}:…`) so Lua scripts and `MULTI` stay in one slot. Active geo-replication is never
   enabled across jurisdictions.
8. **Logs, SIEM and exports.** Process logs and metrics go to a **Log Analytics** workspace in the
   region (inside the EUDB), with data-export rules to **immutable Blob Storage** for retention.
   Audit and decision exports (ADR-0030 §5) target **Blob Storage with a locked time-based
   immutability policy** (WORM, JSON lines) and **Event Hubs** for streaming to Microsoft Sentinel
   and other SIEMs; S3-compatible buckets follow for customers on other clouds.
9. **Secrets and workload identity.** Service secrets live in **Key Vault secrets**, read with
   **Entra Workload ID** on AKS (`azure-identity`, Key Vault Secrets User role). CI deploys use
   **GitHub OIDC federation** to an Entra application or user-assigned managed identity (limit:
   20 federated credentials per identity). Key Vault and Managed HSM diagnostic logs flow to Log
   Analytics as the evidence of every sign and unwrap.
10. **Human identity.** ADR-0024 §4 (product-owner decision 2026-10-18): **Auth0** for UMAI Cloud,
    one Auth0 tenant per region; Keycloak only in self-hosted installs. Whether the EU tenant runs
    in Auth0's public cloud (delivered on AWS) or as Auth0 Private Cloud on Azure (Germany and the
    USA, contract pricing) is Q35.
    **Microsoft Entra External ID was evaluated and rejected** as the broker: no organization
    object (one shared tenant, or at most 20 external tenants per subscription), no customer
    self-service SSO setup, **no inbound SCIM**, 7-day log retention unless exported, no TOTP.
    It may later appear as one more upstream IdP behind the broker.
11. **EUDB tenant pinning.** Pinning a *new* Entra tenant to the EU Data Boundary is irreversible
    and blocks deployments outside EUDB regions from that tenant; if chosen, the US stack needs
    its own tenant (launch-plan P12).
12. **Assurance.** Azure's SOC 2 Type 2 reports and quarterly bridge letters (Service Trust Portal)
    are the inherited-control evidence for the physical and infrastructure criteria (CC6.4, parts
    of CC6.6, CC7.5) in our SOC 2 program.

## Alternatives considered

| Option | Why not |
|---|---|
| AWS (previous recommendation) or GCP | Product-owner constraint; the plan is provider-neutral where it can be (`Signer`, `UsageSink`, `IdentityBroker`) |
| Keep Ed25519 in SaaS through HCP Vault Dedicated on Azure | Adds a vendor and sub-processor; not offered in Sweden Central or Germany West Central; HashiCorp lists Ed25519 in Transit as not FIPS-compliant |
| Managed HSM for everything | ≈ $2.3k per month per region for a single-tenant HSM nobody has asked for; Key Vault Premium EC-HSM keys are already FIPS 140-3 Level 3 |
| Azure Front Door in front of all hostnames | TLS terminates at global edge points outside the EU Data Boundary — the exact failure ADR-0026 rejects |
| Azure Cache for Redis | Retiring (Basic/Standard/Premium 2028-09-30, Enterprise 2027-03-31) |
| Azure Container Apps for everything | Simpler, but no Kubernetes NetworkPolicy for the isolation tests; may host stateless parts later |
| Entra External ID as the identity broker | No organizations, no self-service SSO, no inbound SCIM; see §10 |
| Germany West Central or France Central as EU primary | Their pairs are access-restricted; workable only with a support request or read-replica DR |

## Consequences

- ADR-0027 §1–§2: two algorithms in the registry, `kms_azure` signer, no AWS recommendation;
  §5–§7 envelope and secrets as in §3 and §9. ADR-0025: Flexible Server specifics and the
  `azure_pg_admin` check. ADR-0026: regions, Application Gateway, Front Door exclusion, EUDB
  tenant. ADR-0023/0028: Azure Managed Redis. ADR-0030: export and log targets. ADR-0024:
  Entra External ID in alternatives. Launch plan S0 items 6–9 and PRR rows name Azure services.
- **S0 work added:** manifest `alg` field and dual-algorithm verifier; `kms_azure` signer with
  client-side digest and signature normalisation; CMK and geo-backup decided at Postgres creation.
  Code for the signer is out of scope of this documentation pass.
- `reference/key-management-services.md` consequence 1 ("Ed25519 can stay if AWS…") is
  superseded for SaaS; the reference keeps the provider-neutral analysis.
- Open for the product owner: the EU region pair (P2), the P-256 switch (P10), image signing and
  Defender for Containers (P11), EUDB tenant pinning (P12).

## Validation

- Signature test: a manifest signed by `kms_azure` verifies with our verifier (raw `r||s` and
  DER inputs); an Ed25519-signed self-hosted bundle verifies through the same registry.
- Role test (ADR-0025 I3) extended: application role not in `azure_pg_admin`, `NOBYPASSRLS`, owns
  no tenant table.
- Provisioning record: zone-redundant HA availability in West Europe / North Europe at creation;
  CMK and geo-backup enabled at creation; both recorded in the S0 PRR.
- IaC policy test: no Front Door profile has a content hostname as origin; Application Gateway
  policy is `AppGwSslPolicy20220101S`; TLS scan shows 1.2/1.3 only with HSTS.
- Restore drill in North Europe (geo-backup restore and read-replica promotion) within the RTO
  with the isolation tests green on the restored server.
- Revisit if Azure adds Ed25519 to Key Vault (SaaS could return to one algorithm), if Image
  Integrity on AKS becomes GA with a deny effect, or if a customer requires Managed HSM.
