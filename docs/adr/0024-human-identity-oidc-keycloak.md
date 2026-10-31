# ADR-0024 — Human identity: standard OIDC in the console, Auth0 for UMAI Cloud, Keycloak for self-hosted, a broker interface

- **Status:** Proposed (§1–§3 and §5–§8 await product-owner approval; §4 decided by the product owner on 2026-10-18)
- **Date:** 2026-10-17 · **Amended:** 2026-10-18 (§4: Auth0 for UMAI Cloud, Keycloak for self-hosted; answers Q29, opens Q35);
  2026-10-30 (§2: revocable console sessions, a session record per sign-in in the policy service; Proposed);
  2026-10-31 (§3: MFA for every console sign-in, ADR-0053; Proposed)
- **Amended by:** ADR-0033 (accepted 2026-10-18), which moves the `IdentityBroker` of §5 from the console to the policy service and defines the organization model of §4; ADR-0053 (Proposed), which widens §3 to every person
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
   (TOTP or WebAuthn) and checked in the console through the `amr`/`acr` claims. *(Amended
   2026-10-31 by ADR-0053: every person, every sign-in; see the amendment at the end.)*
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

## Amendment 2026-10-30: revocable console sessions (§2)

**Status of this amendment:** Proposed. Contract: [console-sessions](../contracts/console-sessions.md)
0.1; credentials 0.8 (the assertion's `session_id`).

**Context.** The console's session lives only in its encrypted cookie (`umai-control-center`
`docs/sign-in.md`): a stolen cookie works until the 12 h lifetime ends, sign-out deletes the
cookie in one browser only, and nothing can end a session elsewhere (gap analysis 7.4.1–7.4.5,
launch plan S0-5). §2 asked for a server-side session store "(Redis or Postgres)".

**Decision.**

1. **A session record per sign-in, kept by the policy service** in the global, allowlisted table
   `console_sessions` (tenant-store migration 0011; ADR-0025 §2): our id (shown to the person), the
   SHA-256 of the session id, the person (`user_id`), created, last seen, expires (created + 12 h),
   a coarse device label, revoked at and why. The session id is 256 random bits made by the
   console at `/auth/callback` and kept inside the encrypted cookie; the store never holds it,
   only its hash. Global, not tenant-scoped: a console session belongs to a person, exists before
   an organization is chosen (onboarding) and moves with the organization switcher.
2. **No IP address, no user agent.** The console turns the `User-Agent` into a label such as
   "Firefox on Windows" and sends only that; the user agent stays in the console. A person
   recognises their devices by the label, the browser and the times; an IP address would be
   personal data with its own retention and disclosure questions (KVKK, GDPR) for little gain
   (offices, VPNs and mobile networks share and change addresses).
3. **The cookie stays the carrier.** It still holds the display fields and the active
   organization; the record only decides whether the session is alive. No Redis, no new service.
4. **Two checks.**
   - **Policy service, on every call that carries an assertion:** the assertion's `session_id`
     must name a live record of the same person (not revoked, not past `expires_at`, seen within
     the idle limit); otherwise 401 `session_ended`. Every change the console makes for a person
     goes through the policy service, so revocation stops changes at once. The one exception is
     starting the session itself. `UMAI_DE_CONSOLE_SESSIONS`: `required` (default in production)
     also refuses an id without a record; `optional` (default elsewhere: development, tests, the
     transition) refuses only a recorded, ended session.
   - **Console middleware, on every page and API request:** `POST /v1/sessions/check` with the
     console's service credential (the middleware runs on Next.js's Edge runtime, which cannot
     read the assertion key file; checking is an operator action that names no actor). The
     answer is cached per session and organization for **30 s** in the console process. Ended or
     unknown → signed out (cookie cleared; pages go to sign-in, API routes answer 401 with
     `x-umai-sign-in: required`).
   - **Staleness bound:** a revoked session can still load console pages and use the engine
     relays (Playground) for at most 30 s per console process; its policy-service calls fail at
     once.
5. **Policy service unreachable** (connection error, 2 s timeout, 5xx): the console never signs
   anyone out for an outage. Changing requests (`POST`, `PUT`, `PATCH`, `DELETE`) answer 503;
   reads (`GET`, `HEAD`) continue for a session confirmed live within the last 5 minutes and
   answer 503 otherwise. Fail closed for changes, a short grace for reads.
6. **Last seen** is written at most every 5 minutes per session (by the check and by assertion
   calls). The policy service ends a session not seen for 70 minutes: the console's 60-minute
   inactivity plus the write interval and the cache, so it never ends a session the cookie still
   holds and the console's 60 minutes stay the effective limit. Absolute end: 12 h.
7. **Self-service:** a person lists their live sessions (the current one marked), ends one, ends
   all others ("Sign out other devices"), and sign-out ends the current record. At most 10 live
   sessions per person; starting another ends the oldest (reason `limit`).
8. **Organizations re-check, they do not end sessions.** The check carries the cookie's active
   organization and answers whether the person is still an active member of an active
   organization. A person removed from an organization (`DELETE …/members/{user}`), or whose
   membership or organization is suspended, loses that organization in the console within 30 s
   (the middleware drops it from the cookie and `/auth/continue` picks another or onboarding);
   their policy-service calls in it fail at once (roles come from the store, ADR-0033 §1). They
   stay signed in to their other organizations.
9. **Retention:** a record is deleted 30 days after its `expires_at` (one indexed delete when a
   session starts). Records are account metadata (no content, no IP).
10. **Transition:** cookies from before the change carry no session id and count as signed out:
    everyone signs in once after the console update. Ship the policy service and the console
    together, or set `UMAI_DE_CONSOLE_SESSIONS=optional` until the console is updated.

**Alternatives considered.**

| Option | Why not |
|---|---|
| Redis (or a table) holding the whole session, the cookie only an id | A new service on the single VM and in every region for state the cookie already carries well; the only missing property was revocation, which the record gives |
| A revocation list only (store ended ids) | Unknown sessions would pass, and a person could not see or end their other devices: listing needs a record per session anyway |
| Check every request without a cache | A policy-service round trip on every page and API call; 30 s bounds the staleness and the policy service's own check is immediate |
| The console signs an assertion for the check | The Edge middleware cannot read the key file; the operator credential is what the console already holds there |
| End the person's sessions when they are removed from an organization | Sessions are per person, not per organization: it would sign them out of their other organizations; the check answers membership instead |
| Store the IP address next to the device label | See 2 |

**Consequences.** Every policy-service call for a person costs one indexed read (and a write at
most every 5 minutes). The console depends on the policy service for every request (it did for
almost every page already); an outage blocks changes and, after 5 minutes, reads. Still open
from §2: an admin ending another person's sessions (organization-wide termination, ASVS 7.4.5),
re-authentication before sensitive changes (7.5.x), a separate "sign out everywhere" that also
ends the identity service's sessions.

**Validation.** Engine: `tests/integration/test_console_sessions.py` (start, list, end one, end
others, sign-out, the limit, idle and absolute expiry, member removal answered by the check,
`required` / `optional`, the authz matrix) and `tests/db/test_isolation.py` (the global table and
its grants on Postgres). Console: `tests/session-check.test.mjs` (cache bound, outage rules,
device labels) in `umai-control-center`.

## Amendment 2026-10-31: MFA for every console sign-in (§3)

**Status of this amendment:** Proposed. Decision record: [ADR-0053](0053-mfa-for-every-console-sign-in.md)
(launch plan S0-6).

**Change to §3.** MFA is required for **every person at every console sign-in**, not only for
`owner` and `admin`; the Enterprise tenant's choice to require it for all members is therefore
moot while this holds. The identity service asks for the second factor and holds it (Auth0: policy
*Always*, authenticator app plus recovery code, WebAuthn once the plan's tier is confirmed; a
post-login Action turns the console's multi-factor `acr_values` into a challenge that a remembered
browser cannot skip). The console checks **`amr`** (`mfa`), not `acr`, at the callback and starts
no session without it; a deployed (`https`) console cannot switch the check off. A person enrols at
their first sign-in. Factor resets are done by UMAI support after an identity check outside the
product, never by an organization's admin. The policy service enforces nothing new until MFA
becomes role-dependent or Enterprise SSO arrives (ADR-0053 Decision 4).

**Why not role-dependent, as §3 was written.** Roles live in our store and the active organization
is chosen after sign-in, so a role-dependent challenge needs a second trip to the identity service
for owners (most Stage 0 users), checks wherever the organization changes, and lets enrolment wait
until a promotion, when whoever holds the password can enrol their own factor first.

**Still open from §2 and §3:** re-authentication before changing e-mail or MFA (Auth0's hosted
pages offer no self-service factor change yet); ending a person's console sessions after a factor
reset; Keycloak's `amr` values for OTP.

