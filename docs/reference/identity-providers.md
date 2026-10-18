# Identity providers and auth brokers for the console (SSO, SCIM, audit)

**Last verified:** 2026-10-17 · **Used by:** ADR-0024 (human identity plane), ADR-0026 (residency of account metadata) ·
Labels: see [README](README.md). Everything is a **vendor claim** from the vendor's own pages
unless marked otherwise; prices are list prices on the day and change.

Constraint that shaped the comparison: the console must run as UMAI Cloud (EU and US
regions, ADR-0018) **and** inside a customer's network (self-hosted), from one codebase.

## Comparison

| | WorkOS | Auth0 (Okta CIC) | Stytch | Keycloak | Zitadel |
|---|---|---|---|---|---|
| SAML 2.0 / OIDC enterprise SSO | Yes / yes; connection per organization | Yes / yes (Okta WF, Entra ID, Google WS, ADFS, LDAP, PingFederate) | Yes / yes; per organization | Yes / yes; brokers to external SAML/OIDC IdPs | Yes / yes; external OIDC/SAML/LDAP IdPs |
| Customer self-service SSO setup | Admin Portal (domain verify, SSO, directory sync, log streams) | Self-Service SSO profiles + ticket URL, incl. SCIM | Admin Portal React components | None in core; admin console / REST API (third-party Phase Two adds one) | Console; delegated per-organization IdP management |
| SCIM | Directory Sync, custom SCIM 2.0; Okta, Entra ID, Google, JumpCloud, OneLogin, Rippling, PingFederate, CyberArk, SailPoint | Inbound SCIM 2.0 on SAML/OIDC/Okta/Entra connections | SCIM 2.0 per organization; Okta and Entra guides; groups → roles | SCIM API **supported since 26.8.0 (2026-10-01)**; realm-scoped, users and groups | SCIM 2.0 server, **users only** (no groups); GA status *unverified* |
| Customer-facing audit logs / SIEM | Per-organization Audit Logs product; CSV export; log streams to Datadog, Splunk, S3, GCS, Sentinel, Snowflake, HTTPS | Tenant logs (5–30-day retention); streams to EventBridge, Event Grid, Datadog, Splunk, Sumo, Elastic, webhook; no per-organization product | Project event logs (30 days); Datadog / Loki streaming; per-org audit logs on the public roadmap | User and admin events with expiration; Listener SPI for export; no SIEM connector | Event-sourced trail, unlimited retention, Events API; actions → webhook; no SIEM connector |
| Self-hosting | **No** (SaaS only; "on-prem" doc means your app calls WorkOS) | **No** customer-hosted option (Private Cloud is Auth0-operated) | **No** | **Yes**, Apache 2.0, CNCF incubating; Red Hat build with paid support | **Yes** (Docker, Helm, PostgreSQL); core **AGPL-3.0 since v3 (2025-03-31)**, commercial licence offered |
| EU region | **No**: DPA names the USA as the only storage/access country | Yes: US, EU, AU, JP, UK, CA | **No**: "operates exclusively out of U.S. servers" | Your infrastructure | Cloud: US, EU, Switzerland, Australia; EU available on Free |
| Price points | SSO and directory sync $125/connection/month (1–15), down to $65 (51–100); 1M MAU free then $2,500/month per extra 1M; log streams $125/month per SIEM connection | Free: 25k MAU, 1 enterprise connection; Essentials $150–3,800/month; Professional $800–2,400/month; extra enterprise connection $100/month (max 30); inbound SCIM included | Free: 10k MAU, 5 SSO-or-SCIM connections; extra connection $125; overage *unverified* | Free; Red Hat subscription price *unverified* | Free: 100 DAU, EU region; Pro $100/month for 25k DAU; per-connection price *unverified* |
| Python | Official `workos` | Official `auth0-python` | Official `stytch` | No official SDK; standard OIDC (JWKS); community `python-keycloak` (MIT) | Official management client (incubating); user auth via standard OIDC |
| Tenant model | Organizations; org-scoped roles | Organizations; roles per member; connections per org | Organizations, members, RBAC | Organizations inside a realm (since 26.0): per-org IdPs, domain routing, `organization` claim; **no org-scoped roles in core** | Organizations are tenants; project roles; delegated role grants |
| Compliance claims | SOC 2 Type 2; HIPAA BAA (enterprise); ISO 27001 *unverified* | SOC 2 Type 2; ISO 27001/27017/27018; CSA STAR; PCI DSS | SOC 2, ISO 27001 (Twilio trust center) | None (software; your deployment is audited) | ISO/IEC 27001:2022; SOC 2 Type II (announced 2026-01-21); DPF |

One line each: **Clerk** — SaaS only, US-hosted, SAML/OIDC $75/connection/month (2–15).
**Ory** — Kratos and Polis Apache 2.0 and self-hostable (Polis = SAML/OIDC SSO + SCIM);
Ory Network SAML/SCIM are Enterprise-only.

## What this means for us

- **The self-hosted edition rules out WorkOS, Auth0 and Stytch as dependencies.** Only
  Keycloak and Zitadel can run inside a customer's network.
- **An EU SaaS region rules out WorkOS and Stytch** (US-only processing) even for the cloud
  edition; Auth0 and Zitadel Cloud have EU regions; self-operated Keycloak is wherever we run it.
- **One codebase pattern:** the console and the services speak only standard OIDC
  (authorization code + PKCE, JWT validation against the issuer's JWKS, an organization claim
  for tenant selection). Broker-specific operations (create organization, mint an SSO/SCIM
  setup link, read directory events) sit behind an `IdentityBroker` interface with one
  implementation per backend. Roles live in our own database (AGENTS rule 3), which
  neutralises Keycloak's lack of organization-scoped roles.
- **Customer-facing audit logs and SIEM export are our own feature** (ADR-0030), fed from our
  event streams; only WorkOS sells them per organization and it is excluded by hosting.
- Keycloak's SCIM support is three weeks old at verification time; Zitadel's SCIM has no
  groups. Enterprise SCIM provisioning should be verified against Okta and Entra ID test
  tenants before it is sold (verification plan §2).
- **Decision (ADR-0024 §4, product owner, 2026-10-18):** Auth0 for UMAI Cloud, Keycloak for
  self-hosted. Public cloud EU or Private Cloud on Azure is Q35.

## Microsoft Entra External ID (Azure)

Added because UMAI Cloud must run on Azure (product-owner constraint). A separate section
because the comparison table is already five columns wide. Rows match the table above; the
last column covers the alternative "our own **workforce** tenant + B2B collaboration".
Microsoft vendor claims from learn.microsoft.com unless marked; *inference* = our reading,
not a vendor statement.

**Product status.** External ID in an *external tenant* is Microsoft's CIAM product (tenant
type "external", sign-in host `<tenant>.ciamlogin.com`). **Azure AD B2C is no longer sold to
new customers since 2025-05-01**; it is not an option for us.

| | Entra External ID (external tenant) | Workforce tenant + B2B collaboration (alternative) |
|---|---|---|
| SAML 2.0 / OIDC enterprise SSO | SAML/WS-Fed federation: yes (WS-Fed tested only with AD FS, Shibboleth); custom OIDC federation: yes (`code` flow only; `client_secret_post`/`client_secret_jwt`, no `private_key_jwt`); Entra ID tenants via OIDC. Domain-based routing: an email matching a federated domain is redirected to that IdP; `domain_hint` routing for invitations. One "domainless" SAML IdP per tenant. Max number of federations: *unverified* (a 1,000 figure appears only in secondary/stale copies) | SAML/WS-Fed federation per partner; Entra-to-Entra uses native B2B (direct SAML between two Entra tenants unsupported); configurable redemption order (not available in external tenants) |
| Tenant model | **No organization object.** Options: one external tenant for all customers (customer org = groups / app roles / custom attribute, max 100 custom attributes per user) or one external tenant per customer (**max 20 external tenants per Azure subscription**; 300,000 objects per tenant, raisable via support). App registrations are single-tenant only | Guests are user objects in our directory; no per-customer org object either; cross-tenant access settings are per partner tenant |
| Customer self-service SSO setup | **None.** IdP setup needs the *External Identity Provider Administrator* role in our tenant (admin center or Graph `samlOrWsFedExternalDomainFederation` / identity-provider APIs). A self-service portal would be ours to build on Graph (*inference*) | None (same roles/APIs) |
| SCIM | **No inbound SCIM into external tenants.** Entra's provisioning service is a SCIM *client* (outbound to apps); "Provisioning logs: not available" in external tenants; cross-tenant sync not available in external tenants. The new Entra **SCIM Provisioning API** (`graph.microsoft.com/rp/scim`, paid per call, needs Entra ID P1, app-only token with tenant-wide `User.ReadWrite.All`) is documented for workforce tenants only; external-tenant support *unverified*, and it is tenant-wide, not per customer org | Cross-tenant synchronization: push from a customer's **Entra** tenant only, P1 per synced user on the customer side; Microsoft says it is "intended for use within an organization". Non-Entra IdPs (Okta, Google): no inbound SCIM except the paid SCIM API above |
| MFA | Email OTP, SMS (paid add-on), passkey/FIDO2 (needs custom URL domain; not for federated or email-OTP-primary users). **No TOTP / authenticator app listed.** Conditional Access: limited (all users incl./excl.; conditions = device platform, location; grant = block / MFA / password reset; sign-in frequency, persistent session). ID Protection (risk) not available | Full Entra MFA methods and Conditional Access for guests; MFA trust of the guest's home tenant via cross-tenant access settings and its licensing: *unverified* (not checked in this pass) |
| Customer-facing audit logs / SIEM | Audit and sign-in logs; **7-day retention** in external tenants; export via Azure Monitor diagnostic settings (Log Analytics, Event Hubs, Storage) set up through an Azure Lighthouse wizard from a workforce tenant; Sentinel only from the workforce-tenant workspace (feature table still labels this preview). No per-customer audit product | Standard Entra audit/sign-in logs and diagnostic settings |
| Self-hosting | No (SaaS) | No |
| EU / US region | Country/Region chosen at tenant creation, **cannot be changed**; maps to geo-locations incl. **EMEA** and **North America**. So one EU tenant and one US tenant is possible. Go-Local (in-country) only Australia, Japan. Entra EU Data Boundary commitments exist with documented exceptions (MFA data "North America and/or in geo"; fraud IPs/phones published globally); whether every EUDB statement applies identically to external tenants: *unverified* | Same Entra geo model |
| Price points (USD list, Azure retail price API) | Free first **50,000 MAU** (all users in an external tenant count, admins included); "Basic" **$0.03 per MAU** above 50k. Add-ons (no free tier): SMS $0.03 / $0.07 / $0.15 / $0.35 per attempt by country band (Türkiye and US low; DE, NL mid-high); M2M (client credentials) $0.001 per token; Go-Local $0.02/MAU. MAU is aggregated across all tenants linked to a subscription. A separate "Core MAU" meter ($0.01625 above 50k) exists in the price API; what it maps to is *unverified*. Custom URL domain needs Azure Front Door (billed separately) | Guest MAU billing (same meters); internal staff need Entra licences; ID Governance for guests $0.75/MAU |
| Python | Standard OIDC; MSAL Python; Microsoft Graph SDK | Same |
| Token / branding | Claims mapping, claims transformation, custom claims provider (`TokenIssuanceStart` REST extension, **2,000 ms timeout, 1 retry, 50 RPS per tenant**) → an `org_id` claim from our DB is feasible; app roles and group claims (object ID only). Company branding, language customisation; custom URL domain via reverse proxy (Front Door); blocking the default domain needs a support ticket | Claims mapping / custom claims provider; branding applies to Microsoft sign-in pages |
| Limits | 200 requests/s per tenant, 20/s per IP (trial: 20/s); sign-in ≈ 4 requests, sign-up ≈ 6 | Workforce limits (not checked) |
| Compliance / DPA | Azure ISO/IEC 27001 and SOC 2 Type 2 programmes cover "Azure"; the named in-scope list is in the Service Trust Portal appendix, so External ID being named in scope is *unverified*. Same vendor and Products and Services DPA as Azure, so likely no new sub-processor *(inference; DPA coverage of External ID unverified)* | Same |

Also verified, one line each:
**Keycloak on Azure** — no managed offering: Red Hat states "there is no Red Hat build of
Keycloak as a managed-service offering"; it is entitled and supported (not operated) on Azure
Red Hat OpenShift; Red Hat support on AKS *unverified* (knowledge-base article behind login).
**Auth0 Private Cloud on Azure** — a dedicated single-tenant Auth0 instance operated by Okta
on Azure, deployable in Australia, Canada, Germany, Switzerland, UK, USA; 99.99% SLA; price
not published.
**Auth0 public cloud hosting** (checked 2026-10-18) — Auth0 describes its public cloud as
delivered on AWS, with regions US, UK, EU, Japan, Australia and Canada (one language version of
the page omits Canada) and a 99.99% SLA
([auth0.com/platform/cloud-deployment/aws](https://auth0.com/platform/cloud-deployment/aws));
Private Cloud is deployable on Azure or AWS. EU location Frankfurt with failover to Dublin comes
only from a community-forum post (*secondary*). That every public-cloud EU tenant runs on AWS is
*unverified* (no sub-processor page checked).

### What this means for us (Azure constraint)

- **Entra External ID cannot replace Keycloak/Auth0 for per-customer enterprise SSO + SCIM.**
  Federation (SAML/OIDC, domain routing) works, but there is no organization model, no
  customer self-service SSO setup, and **no inbound SCIM** from a customer's Okta / Entra /
  Google into an external tenant. Building orgs, a setup portal and a SCIM server ourselves on
  Graph is most of what we would buy a broker for.
- **Tenant-per-customer does not scale** (20 external tenants per subscription, no
  cross-tenant management); one shared tenant means the customer org lives in our database
  and claims come from the `TokenIssuanceStart` extension (2 s budget, on the sign-in path).
- **Advantages:** native Azure (same vendor, same DPA, no new processor), EMEA and North
  America tenants, cheap (50k MAU free, then $0.03/MAU), passkeys, Azure Monitor/Sentinel export.
- **Weak spots for a security product:** 7-day log retention unless exported, no TOTP,
  limited Conditional Access, no risk-based protection.
- **Workforce tenant + B2B** fits Entra-based customers (cross-tenant sync, MFA trust) but is
  not sold for third-party SaaS customers ("intended for use within an organization") and
  still has no SCIM for Okta/Google customers.
- **Azure-compatible paths that keep ADR-0024:** (a) self-operated Keycloak on AKS or Azure Red
  Hat OpenShift, one deployment per region (EU, US), same as self-hosted; (b) Auth0 Private
  Cloud on Azure (Germany / USA) if a managed broker is required, at contract pricing. External
  ID could at most be an extra upstream IdP behind the broker, not the broker. A change of
  direction needs an ADR (amend ADR-0024).

## Sources (accessed 2026-10-17)

WorkOS: workos.com/pricing · /docs/sso · /docs/admin-portal · /docs/directory-sync ·
/docs/audit-logs · /docs/on-prem-deployment · /legal/data-processing-addendum.
Auth0: auth0.com/pricing · /docs/authenticate/enterprise-connections/self-service-SSO ·
/docs/authenticate/protocols/scim/configure-inbound-scim · /docs/customize/log-streams ·
/docs/deploy-monitor/deployment-options · /docs/troubleshoot/customer-support/operational-policies/public-cloud-service-endpoints ·
/docs/manage-users/organizations · /docs/secure/data-privacy-and-compliance.
Stytch: stytch.com/pricing · /docs/b2b/guides/sso/overview · /docs/b2b/guides/scim/overview ·
/docs/resources/workspace-management/event-logs · /docs/resources/policies/security-and-trust/compliance/data-compliance.
Keycloak: keycloak.org/2026/10/keycloak-2680-released · /docs/26.8.0/release_notes ·
/docs/latest/server_admin · github.com/keycloak/keycloak/blob/main/LICENSE.txt ·
docs.redhat.com (Red Hat build of Keycloak 26.2: organizations, auditing).
Zitadel: zitadel.com/pricing · /docs/concepts/structure/organizations · /docs/guides/manage/user/scim2 ·
/docs/concepts/features/audit-trail · /docs/self-hosting/deploy/overview ·
github.com/zitadel/zitadel/blob/main/LICENSE · zitadel.com/blog/apache-to-agpl ·
help.zitadel.com/security-compliance/certifications-and-compliance.
Clerk: clerk.com/pricing. Ory: ory.com/pricing · github.com/ory/polis.
Microsoft Entra External ID (learn.microsoft.com/en-us/entra/…): external-id/customers/concept-supported-features-customers ·
/external-id/customers/overview-customers-ciam · /external-id/external-identities-pricing ·
/external-id/direct-federation · /external-id/direct-federation-overview ·
/external-id/customers/how-to-custom-oidc-federation-customers ·
/external-id/customers/concept-multifactor-authentication-customers ·
/external-id/customers/reference-service-limits · /external-id/customers/reference-group-app-roles-support ·
/external-id/customers/how-to-create-external-tenant-portal · /external-id/customers/how-to-azure-monitor ·
/external-id/customers/concept-custom-url-domain · /fundamentals/data-residency · /fundamentals/data-storage-eu ·
/identity/app-provisioning/known-issues · /identity/app-provisioning/entra-id-scim-api-reference ·
/identity/app-provisioning/enable-scim-api · /identity/multi-tenant-organizations/cross-tenant-synchronization-overview.
Azure AD B2C end of sale: learn.microsoft.com/en-us/azure/active-directory-b2c/overview.
Prices: prices.azure.com/api/retail/prices (filter productName contains "External ID"; USD, Global);
azure.microsoft.com/pricing/details/microsoft-entra-external-id (free 50k MAU; amounts rendered client-side).
Compliance: learn.microsoft.com/en-us/azure/compliance/offerings/offering-iso-27001 · /offering-soc-2 ·
/cloud-services-in-audit-scope.
Keycloak on Azure: access.redhat.com/articles/7044244. Auth0 on Azure:
auth0.com/docs/deploy-monitor/deploy-private-cloud/private-cloud-on-azure.
