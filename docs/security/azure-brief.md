# Azure brief: UMAI Cloud runs on Azure

**Status:** 2026-10-17. Product-owner constraint, not a recommendation: **UMAI Cloud is hosted on
Azure.** Applied the same day: ADR-0031 and the revisions listed in §4 are written (all Proposed). This answers launch-plan §6 **P1** and voids the AWS recommendation there. This brief is
the input for revising the readiness plan (ADR-0023 to ADR-0030, [launch plan](launch-plan.md));
it does not itself change those documents.

Facts (verified 2026-10-17, with sources and "unverified" labels):
[`reference/azure-platform.md`](../reference/azure-platform.md) and the "Microsoft Entra External
ID (Azure)" section of [`reference/identity-providers.md`](../reference/identity-providers.md).
Do not restate them from memory; cite those files.

## 1. Decided

| # | Decision | Source |
|---|---|---|
| Z1 | Cloud provider for UMAI Cloud is **Azure** (EU first, US later). Self-hosted is unchanged | product owner |

## 2. Consequences established by the research (to be recorded in ADRs)

| # | Topic | Finding | Proposed change |
|---|---|---|---|
| Z2 | **Signing algorithm** | No Azure key store (Key Vault, Managed HSM) supports Ed25519. Key Vault Premium EC-HSM P-256 (`ES256`) is FIPS 140-3 L3 at about $5/key/month; the digest is computed client-side and the signature encoding must be normalised (SDK encoding *unverified*). HCP Vault would keep Ed25519 but adds a vendor, is not offered in every EU region, and its Ed25519 is not FIPS-compliant. Managed HSM (≈ $2.3k/month/region) only if a customer demands a single-tenant HSM | **New ADR** superseding ADR-0027 §1 recommendation: SaaS signs with `ecdsa-p256` in Key Vault Premium (`kms_azure` signer); the manifest `alg` field becomes required work in S0; Ed25519 stays valid for self-hosted / Vault and for verifying existing bundles. Touches ADR-0012 (bundle signing) |
| Z3 | **Envelope encryption** | No `GenerateDataKey` equivalent: the DEK is generated in our code and wrapped by Key Vault Premium (RSA-OAEP-256; AES-KW there is preview) or by Managed HSM (AES-KW) | ADR-0025 §4 / ADR-0027: wrap with RSA-OAEP-256 in Key Vault Premium |
| Z4 | **Regions** | EU primary **West Europe**, DR **North Europe**: the only EU pair where both regions need no access request and Postgres geo-backup stays in the EU. Risk: the Postgres region table flags zone-redundant HA in both with a temporary block (meaning of the flag *unverified*). Fallback: **Sweden Central** primary, cross-region read replica in North Europe as DR. US later: East US 2 ↔ Central US | ADR-0026 and launch plan S0/S1; check the HA flag at provisioning time and record the outcome |
| Z5 | **No Azure Front Door on content paths** | Front Door is excluded from the EU Data Boundary (TLS terminates at global edge points) | ADR-0026: content endpoints (`*.api`, `*.console`) behind a regional **Application Gateway WAF_v2** (`AppGwSslPolicy20220101S`, HSTS via rewrite rule). Note: an Entra External ID custom domain would require Front Door, another reason not to use it (§3) |
| Z6 | **Postgres** | Azure Database for PostgreSQL Flexible Server 17/18, PITR ≤ 35 days; geo-backup only to the paired region, must be enabled at creation, and cannot do PITR; cross-region read replicas allowed. RLS works; verify the app role is not a member of `azure_pg_admin` and has no `BYPASSRLS` (isolation test I3) | ADR-0025, launch plan S0-14, S1-7 (restore drill) |
| Z7 | **Redis** | Azure Managed Redis; Azure Cache for Redis retires 2028-09-30 (Enterprise tier 2027-03-31) | ADR-0028 quotas / ADR-0023 rate limits name Azure Managed Redis |
| Z8 | **Compute and supply chain** | AKS (Kubernetes NetworkPolicy needed for the network-policy tests); Container Apps possible for stateless parts. ACR + **Notation** for signing (AKS image verification preview, audit-only, Notation-only; cosign on ACR *unverified*); Defender for Containers for scanning | Launch plan S0 item 7: "cosign" → Notation (or keep cosign for customer-side verification of self-hosted images; decide) |
| Z9 | **Secrets and workload identity** | Key Vault secrets; Entra Workload ID / managed identity; GitHub Actions OIDC federation to Entra | ADR-0027 §7, launch plan S0 item 6 |
| Z10 | **Logs, SIEM, exports** | Azure Monitor / Log Analytics; Event Hubs; Microsoft Sentinel; Blob Storage with locked immutability policy for audit exports (Blob S3 compatibility *unverified*) | ADR-0030 §5: first export target "S3-compatible bucket" → Azure Blob (immutable) + Event Hubs; keep S3-compatible as a later target for customers |
| Z11 | **Assurance** | Service Trust Portal provides Azure SOC 2 Type 2 reports and bridge letters | Verification plan / SOC 2 evidence: inherited controls from Azure |

## 3. Human identity on Azure (ADR-0024)

Entra External ID was evaluated as a third option. **It does not replace Keycloak or Auth0** for
per-customer-organization SSO/SCIM:

- no organization object (one shared tenant with orgs in our DB, or one tenant per customer,
  capped at 20 external tenants per subscription);
- no customer self-service SSO setup (adding an IdP needs an admin role in our tenant);
- **no inbound SCIM** into external tenants;
- logs kept 7 days unless exported, no TOTP authenticator, limited Conditional Access, no ID
  Protection.

Advantages (SAML/WS-Fed + OIDC federation with domain routing, EU/US tenants, passkeys, Sentinel
export, same vendor as the cloud, 50k MAU free then $0.03/MAU) do not outweigh the blockers.

**Proposal:** ADR-0024 keeps **Keycloak as default, self-operated on AKS** in each region (no
managed Keycloak on Azure; Red Hat supports its build on Azure Red Hat OpenShift, support on AKS
*unverified*), **Auth0 EU as the fallback** (Auth0 Private Cloud on Azure exists, no public
price). Add Entra External ID to "Alternatives considered" with the blockers above; it may later
appear only as one more upstream IdP behind the broker.

## 4. Documents to revise

| Document | Change |
|---|---|
| New ADR (next free number, 0031) "UMAI Cloud on Azure" | Z1–Z11; supersedes the AWS assumptions; status Proposed until the product owner accepts |
| ADR-0024 | §3 above |
| ADR-0025 | AWS SaaS Lens reference stays as background; Postgres Flexible Server specifics (Z6), DEK wrapping (Z3) |
| ADR-0026 | Regions (Z4), Application Gateway, Front Door exclusion (Z5) |
| ADR-0027 | Signer mapping: `kms_azure` P-256 for SaaS; remove "Recommendation: AWS KMS"; costs from the Azure reference (Z2, Z3, Z9) |
| ADR-0028, ADR-0023 | Redis = Azure Managed Redis (Z7) |
| ADR-0030 | Export targets and log pipeline (Z10) |
| `launch-plan.md` | §6 P1 → "Azure (decided)"; P3 notes Entra External ID evaluated and rejected; S0 items 6–8 and PRR rows S0-8/9/10/14 name Azure services; S1-3 and S1-7 name the regions; S2 SIEM target; §5 sequencing unchanged except "cloud + region decision" → "region decision (Z4)" |
| `verification-plan.md` | Residency row: Azure region documentation, EU Data Boundary; supply chain row: Notation/cosign per Z8 |
| `threat-model.md` | Mitigations naming "KMS/secrets manager" → Key Vault; Front Door as a residency threat (Z5) |
| `docs/architecture/deployment-and-editions.md` | SIEM export first target (line about the S3-compatible bucket) |
| `docs/open-questions.md`, `docs/adr/README.md` | Record P1 answered; index the new ADR |

## 5. Still open for the product owner

- Accept West Europe / North Europe (or Sweden Central fallback) as the EU stack.
- Accept the P-256 switch for SaaS signing (Z2).
- Container image signing: Notation only, or Notation + cosign for self-hosted customers (Z8).
- P3–P9 of the launch plan remain open as before.
