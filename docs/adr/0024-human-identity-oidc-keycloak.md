# ADR-0024 — Human identity: standard OIDC in the console, Auth0 for UMAI Cloud, Keycloak for self-hosted, a broker interface

- **Status:** Proposed (§1–§3 and §5–§8 await product-owner approval; §4 decided by the product owner on 2026-10-18)
- **Date:** 2026-10-17 · **Amended:** 2026-10-18 (§4: Auth0 for UMAI Cloud, Keycloak for self-hosted; answers Q29, opens Q35)
- **Deciders:** engineering proposal; product owner
- **Builds on:** ADR-0016 (console repository), ADR-0018 (SSO is Enterprise; self-hosted edition), ADR-0023 (two identity planes), ADR-0026 (residency)
- **Facts:** [`reference/identity-providers.md`](../reference/identity-providers.md) incl. the "Microsoft Entra External ID (Azure)" section (verified 2026-10-17) and Auth0 hosting (verified 2026-10-18); hosting constraint ADR-0031

## Context

The console has no login: every page and every relay route is open to whoever reaches port
3100 (threat model R1). Enterprise buyers require SSO (SAML or OIDC against their IdP) and
user provisioning (SCIM); ADR-0018 sells both as Enterprise capabilities. Two constraints
decide the shape: the console must run in UMAI's EU and US regions **and** inside a customer's
network from one codebase, and account metadata (names, e-mails) should stay in the tenant's
region where possible (ADR-0026 §5). The reference comparison shows: WorkOS and Stytch process
only in the US and cannot be self-hosted; Auth0 has an EU region but no customer-hosted option;
Keycloak (Apache 2.0) and Zitadel (AGPL-3.0 core) can run anywhere.

The first version of this ADR made Keycloak the default for both modes and left buying Auth0
for UMAI Cloud as an open decision (Q29). The product owner chose Auth0 for UMAI Cloud on
2026-10-18.

## Decision

1. **The console and the services speak only standard OIDC.** Sign-in is OpenID Connect
   authorization code with PKCE against an issuer configured per deployment; tokens are
   validated against the issuer's JWKS with an algorithm allowlist, `iss`, `aud`, `exp`, `nbf`
   checks; the tenant is taken from an organization claim. Nothing in our code depends on a
   vendor SDK for authentication.
2. **Sessions and browser hardening** (ASVS 5.0 V3, V7, Level 2): server-side session store
   (Redis or Postgres), cookie `__Host-session`, `Secure`, `HttpOnly`, `SameSite=Lax`;
   proposed inactivity timeout 60 min and absolute lifetime 12 h; sign-out everywhere; an admin
   can end a user's sessions; re-authentication before changing e-mail or MFA. Mutating relay
   routes check `Origin` and the session; HSTS (≥ 1 year), `Content-Security-Policy` with
   `frame-ancestors 'none'`, `Cache-Control: no-store` on API responses. Every page and route
   is behind the session except sign-in and health.
3. **MFA** is required for `owner` and `admin` in every edition and can be required for all
   members by an Enterprise tenant; enforced by the identity service's authentication policy
   (TOTP or WebAuthn) and checked in the console through the `amr`/`acr` claims.
4. **Identity service: Auth0 for UMAI Cloud, Keycloak for self-hosted** (product-owner
   decision 2026-10-18; answers Q29).
   - **UMAI Cloud:** one Auth0 tenant per SaaS region (EU now, US with the US stack,
     ADR-0026); one Auth0 *organization* per UMAI tenant, with enterprise connections (SAML or
     OIDC) per organization, Self-Service SSO for the customer's admin and inbound SCIM on
     those connections. UMAI operates no identity service in the SaaS regions.
   - **Self-hosted:** Keycloak, shipped as a container. One realm per installation; one
     Keycloak *organization* per tenant (domain routing, per-tenant external IdPs over SAML or
     OIDC); SCIM through Keycloak's SCIM API (supported since 26.8.0). Apache 2.0 keeps the
     Community edition licence-clean.
   - **Reasons:** no managed Keycloak exists on Azure, so the previous default meant operating
     a stateful HA service per region (Red Hat supports its build on Azure Red Hat OpenShift;
     support on AKS *unverified*). Auth0 ships customer self-service SSO setup and inbound SCIM
     today; Keycloak has no self-service setup in core and has had SCIM only since 2026-10-01.
     Auth0 has an EU region. It has no customer-hosted option, so self-hosted keeps Keycloak.
   - **Accepted costs:** two adapters behind §5; Auth0 becomes an identity sub-processor, and
     the account metadata of SaaS console users lives in the region's Auth0 tenant rather than
     in our stack (ADR-0026 §5); enterprise connections beyond the plan cost $100/month each
     (at most 30).
   - **Still open (Q35):** Auth0's public cloud EU region, which Auth0 describes as delivered
     on AWS, or Auth0 Private Cloud on Azure (Germany, contract pricing), given the Azure
     constraint of ADR-0031.

   Microsoft Entra External ID was evaluated and is not an option as the broker (Alternatives).
5. **`IdentityBroker` interface** in the console's server side: create organization, configure
   an SSO connection (IdP metadata upload, DNS-TXT domain verification), enable SCIM (endpoint +
   token per tenant), list directory events, mint an admin setup link. Implementations: `auth0`
   (Management API) for UMAI Cloud, first; `keycloak` (admin REST API) for self-hosted; both
   pass one shared contract test suite; `zitadel` possible. The broker is the only code that
   knows the vendor; sign-in and token validation stay standard OIDC (§1).
6. **Roles are ours.** The identity service delivers identity and organization membership; roles
   and permissions live in our database and are enforced by our services (ADR-0029). Group
   claims from a customer IdP map to our roles through a per-tenant mapping table.
7. **Service calls on behalf of a user** carry the signed on-behalf-of assertion of ADR-0023;
   no human token ever reaches the engine or the policy service.
8. **Staging:** stage 0 (private beta) uses the EU Auth0 tenant with e-mail + password + TOTP
   for design partners, no SSO. The `keycloak` adapter and container ship with the first
   self-hosted release. Stage 2 (Enterprise GA) adds SAML/OIDC SSO and SCIM on both adapters,
   verified against the external test tenants (verification plan §2).

## Alternatives considered

| Option | Why not |
|---|---|
| Keycloak for both modes (this ADR's proposal before 2026-10-18) | One adapter, identity data in our own regional stack, no identity sub-processor — but UMAI operates an HA Keycloak per region (no managed offering on Azure), customer self-service SSO setup is ours to build (none in core) and SCIM exists only since 26.8.0. Rejected for UMAI Cloud by the product owner; kept for self-hosted |
| WorkOS | Best admin portal and per-organization audit logs, but SaaS-only and US-only processing: fails self-hosted and the EU region |
| Stytch | Same two failures (US-only, SaaS-only); per-organization audit logs still on its roadmap |
| Auth0 as the only identity service | No customer-hosted option, so self-hosted needs a second identity stack anyway; hence Auth0 for UMAI Cloud only (§4) |
| Zitadel everywhere (Cloud EU + self-hosted) | One adapter and a managed EU cloud, but the core moved to AGPL-3.0 (licence diligence for the Community edition), SCIM is users-only (no groups) and the Python client is incubating |
| Microsoft Entra External ID (Azure's CIAM) | Same vendor and DPA as the cloud, 50k MAU free, EMEA/North America tenants — but no organization object (one shared tenant, or at most 20 external tenants per subscription), no customer self-service SSO setup, **no inbound SCIM**, 7-day log retention unless exported, no TOTP; at most an upstream IdP behind the broker (ADR-0031 §10) |
| Build our own authentication (passwords, MFA, SAML) | A security product writing its own SAML stack is the wrong risk to take; the standard protocols exist |
| Magic-link / password-only login with a framework helper, no IdP | No path to SSO or SCIM; Enterprise rows of ADR-0018 become a rewrite |

## Consequences

- The SaaS regions run no identity service: an Auth0 tenant per region replaces Keycloak on AKS
  (ADR-0026 §1, ADR-0031 §6). Tenant configuration (connections, MFA policy, callback URLs,
  branding) is kept in version control and applied from CI; the tool is chosen at S0.
- Auth0 joins the per-region sub-processor list and the DPA (Q25). Plan and per-connection fees
  become a cost line (Free: 25k MAU and one enterprise connection; paid plans in
  `reference/identity-providers.md`).
- Two adapters: `auth0` at S0, `keycloak` with the first self-hosted release. The shared contract
  suite keeps them interchangeable; console code outside the broker uses no Auth0-only feature.
- The self-hosted bundle gains a Keycloak container and its database. Keycloak's SCIM (three
  weeks old at the time of writing) is now a risk for self-hosted Enterprise only, verified
  before self-hosted SSO/SCIM is sold.
- The console gains OIDC client code (e.g. `openid-client`), a session store, the security
  headers above, settings pages for SSO/SCIM, and a `members` page (ADR-0029).
- Entitlement gate `sso` (ADR-0019 §6) controls whether a tenant may configure an external IdP.

## Validation

- Browser tests: unauthenticated request to any page or relay route → redirect/401; cookie
  attributes as specified; session ends after inactivity and absolute lifetime; sign-out
  everywhere; CSRF: cross-origin POST to a relay route rejected.
- ASVS 5.0 L2 items of V3, V6, V7, V9, V10 mapped to tests or controls in the gap analysis.
- Broker contract tests against an Auth0 development tenant and a Keycloak container.
- External: SAML and OIDC sign-in and SCIM provisioning against an Okta developer tenant and a
  Microsoft Entra ID test tenant, for both adapters; Okta's SCIM conformance suite; recorded as
  evidence.
- Pentest covering authentication and session management before stage 2.
- Revisit §4 if plan and per-connection fees outgrow the cost of operating Keycloak per region,
  if a customer requires account metadata inside UMAI's own regional stack, or if Q35 rules out
  Auth0's public cloud and Private Cloud on Azure is not affordable.
