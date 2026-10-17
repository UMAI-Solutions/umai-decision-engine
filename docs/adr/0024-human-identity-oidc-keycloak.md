# ADR-0024 — Human identity: standard OIDC in the console, Keycloak as the default identity service, a broker interface for alternatives

- **Status:** Proposed (awaits product-owner approval; the SaaS identity-service choice in §4 is the open decision)
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner
- **Builds on:** ADR-0016 (console repository), ADR-0018 (SSO is Enterprise; self-hosted edition), ADR-0023 (two identity planes), ADR-0026 (residency)
- **Facts:** [`reference/identity-providers.md`](../reference/identity-providers.md) incl. the "Microsoft Entra External ID (Azure)" section (verified 2026-10-17); hosting constraint ADR-0031

## Context

The console has no login: every page and every relay route is open to whoever reaches port
3100 (threat model R1). Enterprise buyers require SSO (SAML or OIDC against their IdP) and
user provisioning (SCIM); ADR-0018 sells both as Enterprise capabilities. Two constraints
decide the shape: the console must run in UMAI's EU and US regions **and** inside a customer's
network from one codebase, and account metadata (names, e-mails) should stay in the tenant's
region where possible (ADR-0026 §5). The reference comparison shows: WorkOS and Stytch process
only in the US and cannot be self-hosted; Auth0 has an EU region but no customer-hosted option;
Keycloak (Apache 2.0) and Zitadel (AGPL-3.0 core) can run anywhere.

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
4. **Default identity service: Keycloak, operated by UMAI on AKS in each SaaS region and
   shipped as a container for self-hosted** (no managed Keycloak exists on Azure; Red Hat
   supports its build on Azure Red Hat OpenShift, support on AKS *unverified*). One realm per region; one Keycloak *organization* per tenant
   (domain routing, per-tenant external IdPs for Enterprise SSO over SAML or OIDC); SCIM through
   Keycloak's SCIM API (supported since 26.8.0). Reasons: it is the only option that satisfies
   both deployment modes with **one adapter**, it is licence-clean for the Community edition
   (Apache 2.0), and it keeps account metadata in region without adding an identity
   sub-processor.
   **Open decision for the product owner:** operating Keycloak per region (HA, upgrades) versus
   buying **Auth0** for the SaaS human plane — EU public cloud, or Auth0 Private Cloud on Azure
   (Germany, contract pricing) — while keeping Keycloak for self-hosted (two adapters, best
   self-service SSO tooling, enterprise connections metered at $100/month each beyond the
   plan). Microsoft Entra External ID was evaluated and is not an option as the broker
   (Alternatives). Trigger to switch: if Keycloak's SCIM does not pass conformance against
   Okta and Entra ID test tenants by the stage-2 readiness review, SaaS SSO/SCIM moves to Auth0.
5. **`IdentityBroker` interface** in the console's server side: create organization, configure
   an SSO connection (IdP metadata upload, DNS-TXT domain verification), enable SCIM (endpoint +
   token per tenant), list directory events, mint an admin setup link. First implementation
   `keycloak` (admin REST API); `auth0` second if §4's trigger fires; `zitadel` possible.
6. **Roles are ours.** The identity service delivers identity and organization membership; roles
   and permissions live in our database and are enforced by our services (ADR-0029). Group
   claims from a customer IdP map to our roles through a per-tenant mapping table.
7. **Service calls on behalf of a user** carry the signed on-behalf-of assertion of ADR-0023;
   no human token ever reaches the engine or the policy service.
8. **Staging:** stage 0 (private beta) uses Keycloak with e-mail + password + TOTP for design
   partners, no SSO; stage 2 (Enterprise GA) adds SAML/OIDC SSO and SCIM, verified against the
   external test tenants (verification plan §2).

## Alternatives considered

| Option | Why not |
|---|---|
| WorkOS | Best admin portal and per-organization audit logs, but SaaS-only and US-only processing: fails self-hosted and the EU region |
| Stytch | Same two failures (US-only, SaaS-only); per-organization audit logs still on its roadmap |
| Auth0 as the only identity service | No customer-hosted option, so self-hosted would need a second identity stack anyway; kept as the SaaS alternative in §4 |
| Zitadel everywhere (Cloud EU + self-hosted) | One adapter and a managed EU cloud, but the core moved to AGPL-3.0 (licence diligence for the Community edition), SCIM is users-only (no groups) and the Python client is incubating |
| Microsoft Entra External ID (Azure's CIAM) | Same vendor and DPA as the cloud, 50k MAU free, EMEA/North America tenants — but no organization object (one shared tenant, or at most 20 external tenants per subscription), no customer self-service SSO setup, **no inbound SCIM**, 7-day log retention unless exported, no TOTP; at most an upstream IdP behind the broker (ADR-0031 §10) |
| Build our own authentication (passwords, MFA, SAML) | A security product writing its own SAML stack is the wrong risk to take; the standard protocols exist |
| Magic-link / password-only login with a framework helper, no IdP | No path to SSO or SCIM; Enterprise rows of ADR-0018 become a rewrite |

## Consequences

- Operations gain a stateful service per region (Keycloak on AKS against the regional
  PostgreSQL Flexible Server, HA pair, upgrade cadence). This is the cost of §4's default; the Auth0 alternative trades it for a
  sub-processor and per-connection fees.
- The console gains OIDC client code (e.g. `openid-client`), a session store, the security
  headers above, settings pages for SSO/SCIM, and a `members` page (ADR-0029).
- The sub-processor list stays shorter; the DPA lists no identity vendor for the default choice.
- Keycloak's SCIM support is three weeks old at the time of writing: a risk to track, with the
  Auth0 fallback defined before it is needed.
- Entitlement gate `sso` (ADR-0019 §6) controls whether a tenant may configure an external IdP.

## Validation

- Browser tests: unauthenticated request to any page or relay route → redirect/401; cookie
  attributes as specified; session ends after inactivity and absolute lifetime; sign-out
  everywhere; CSRF: cross-origin POST to a relay route rejected.
- ASVS 5.0 L2 items of V3, V6, V7, V9, V10 mapped to tests or controls in the gap analysis.
- External: SAML and OIDC sign-in and SCIM provisioning against an Okta developer tenant and a
  Microsoft Entra ID test tenant; Okta's SCIM conformance suite; recorded as evidence.
- Pentest covering authentication and session management before stage 2.
- Revisit §4 at the stage-2 readiness review (SCIM conformance, operating cost), and whenever a
  customer requires identity metadata in region (which favours the default).
