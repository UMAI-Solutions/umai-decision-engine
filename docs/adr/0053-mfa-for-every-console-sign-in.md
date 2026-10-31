# ADR-0053 — MFA for every console sign-in: required at the identity service, enrolled at the first sign-in, proven to the console by `amr`

- **Status:** Proposed
- **Date:** 2026-10-31
- **Deciders:** engineering (launch plan S0-6, "MFA for owner/admin"); product owner to accept
- **Amends:** ADR-0024 §3 (MFA for `owner` and `admin`; amendment of 2026-10-31 there)
- **Builds on:** ADR-0024 §1 (standard OIDC only), §2 and its 2026-10-30 amendment (console
  sessions), §4 (Auth0 for UMAI Cloud, Keycloak for self-hosted), §7 (no human token reaches the
  services); ADR-0029 (roles in our store); ADR-0033 §8 (Actions translate, they do not
  authorize) and its store-first amendment (the active organization is chosen in the console)
- **Facts:** [`reference/identity-providers.md`](../reference/identity-providers.md), "Auth0:
  Actions, MFA, plans" (re-verified 2026-10-31)
- **Implemented in:** `umai-control-center` `identity/auth0/mfa.tf`,
  `identity/auth0/actions/mfa-on-request.js`, `src/lib/auth/mfa.ts`, `src/app/auth/callback`

## Context

Stage 0 lets 2–3 design partners into the console on `umai-beta` (launch plan S0-6: "MFA for
owner/admin", evidence "Auth0 MFA policy screenshot + test"). ADR-0024 §3 asked for MFA for
`owner` and `admin`, enforced by the identity service and checked in the console through
`amr`/`acr`. Today nothing does either: the console accepts any ID token from the Auth0
development tenant (`dev-zqxuqnql6vid3wcf.us.auth0.com`, Free plan as last recorded), which signs
people in with e-mail and password only. A stolen password is a stolen console account: an owner
or admin can create API keys, change guardrails, read decision records and export the
organization.

What decides the shape:

- **Roles are ours, per organization, and the organization is chosen after sign-in.** Roles live
  in the tenant store (ADR-0024 §6, ADR-0029); since the store-first amendment of ADR-0033 the
  console picks the active organization after the callback (`/auth/continue`), and four places
  change it (continue, the switcher, organization creation, invitation acceptance). A person can
  be owner in one organization and viewer in another, and can be promoted mid-session.
- **Almost everyone is an owner.** Creating an organization makes its creator the owner (sign-in
  design S7); a Community organization has `owner`, `admin` and `viewer` only (A14), and an admin
  holds every non-owner role. Viewers exist only by invitation.
- **What Auth0 proves (verified 2026-10-31):** the ID token carries `amr: ["mfa"]` only when an
  MFA challenge ran in that very transaction; not after `prompt=none`, not after a refresh, and
  (expected, not documented) not after a remembered browser or a reused Auth0 session. Auth0's
  own step-up guide uses a post-login Action that turns
  `acr_values=http://schemas.openid.net/pape/policies/2007/06/multi-factor` into
  `api.multifactor.enable`; whether `acr_values` alone triggers MFA is not documented. An
  Action's `allowRememberBrowser: false` prompts "each time they authenticate".
- **Plans:** Auth0's pricing lists no MFA factors on Free; B2B Essentials includes "Pro MFA"
  (Enterprise factors as an add-on). Which factor is Pro is not documented. Universal Login has
  no page for adding a second factor; recovery codes are off by default.
- **Enrolment is a takeover window.** Whoever first enrols a factor on an account owns the second
  factor. If enrolment waits until a person becomes owner or admin, anyone holding that person's
  password before then can enrol their own authenticator and inherit the promotion.

## Decision

1. **Every person uses a second factor at every console sign-in, whatever their role** (option
   (a) of the brief). This is stricter than ADR-0024 §3 (owner and admin only, all members on an
   Enterprise tenant's request); that per-tenant choice becomes moot while everyone has MFA.
2. **The identity service asks for it and holds the factors.** On Auth0 (`identity/auth0/mfa.tf`):
   - Tenant MFA policy **Always** (`auth0_guardian.policy = "all-applications"`): no sign-in to
     the tenant without a second factor; a person without one enrols at their first sign-in,
     which for a new person is the sign-up itself (also for invitation acceptance).
   - Factors: **authenticator app (TOTP)** for everyone, and a **recovery code** shown once at
     enrolment and replaced after use. **Security keys and device biometrics (WebAuthn)** are
     switched on (`mfa_webauthn`) once the plan's tier is confirmed in the dashboard. Not
     offered: e-mail (it is also the password-reset channel, so it adds nothing against someone
     who controls the mailbox), SMS and voice (SIM swap, per-message cost, Enterprise tier), push
     (another vendor app, Enterprise tier).
   - One post-login **Action, "UMAI: MFA on request"**: when the authorization request carries
     the multi-factor `acr_values`, it calls `api.multifactor.enable("any",
     { allowRememberBrowser: false })`. It holds no authorization logic (ADR-0033 §8): it
     translates a standard OIDC request so that the challenge runs in the console's own
     transaction and the ID token carries `amr`. The tenant's post-login binding is managed
     whole by Terraform (`auth0_trigger_actions`).
3. **The console asks and verifies, at the one place a session starts.** `/auth/login` sends
   `acr_values` for multi-factor on every sign-in; `/auth/callback` starts no console session
   unless the validated ID token's `amr` contains `mfa`, and otherwise sends the browser to
   `/auth/signed-out?error=mfa` (no retry loop: a missing proof means the tenant is
   misconfigured). Setting `UMAI_CONSOLE_MFA`: `required` (default) or `off`, and `off` is
   refused for an `https://` console URL, so a deployed console never runs without MFA; `off`
   exists for a local issuer without MFA.
4. **The policy service enforces nothing new at Stage 0.** It never sees the ID token
   (ADR-0024 §7), so anything it checked would be the console's own statement, signed with the
   same key as the on-behalf-of assertion it already trusts. Every console session starts at the
   callback, which refuses a sign-in without the proof, and a deployed console cannot switch the
   check off. **Revisit** when MFA stops being universal (step-up per role, option (b) below) or
   when Enterprise SSO brings sign-ins whose second factor happens at the customer's IdP: then
   the console sends the session's MFA time with `POST /v1/me/sessions` (contract
   console-sessions 0.2) and the policy service refuses owner and admin actions of a session
   without it (`403 mfa_required`).
5. **Recovery.**
   - Lost device: the recovery code; Auth0 shows a new one.
   - Lost device and recovery code: **UMAI support** resets the person's factors (Auth0
     dashboard *Reset MFA*, or `DELETE /api/v2/users/{id}/authentication-methods`, scope
     `delete:authentication_methods`, which the Terraform application does not hold) after
     checking who they are outside the product: for a member, another owner of their
     organization confirms; for a sole owner, a call with the contact named in the beta
     agreement. The person enrols again at their next sign-in and should end their other console
     sessions (Sessions page).
   - **Organization admins cannot reset factors.** A person's account spans every organization
     they belong to; an admin of one organization resetting a factor would weaken the person's
     account in organizations the admin has no rights in.
6. **Enrolment** happens only on Auth0's hosted pages (scan the QR code or type the secret, enter a
   code, keep the recovery code); UMAI never sees a factor secret, code or recovery code. Existing
   accounts of the development tenant enrol at their next sign-in after the change. Adding a
   second factor or replacing the authenticator without losing it is not offered at Stage 0
   (Universal Login has no page for it; it needs an `enrollWith` Action or Auth0's My Account
   API).
7. **Self-hosted (Keycloak)** keeps the same console contract: the realm requires OTP in its
   browser flow and the console checks `amr`. Which `amr` values Keycloak 26 emits for OTP is
   checked when the `keycloak` adapter ships, and the console's accepted values are extended then
   (ADR-0024 §3: MFA is required in every edition).

## Alternatives considered

| Option | Why not |
|---|---|
| (b) Step-up for owners and admins only: the console reads the person's roles in the active organization and, without `amr`, sends them back with `acr_values` (and `max_age`) | Two round trips to Auth0 for most Stage 0 users (every organization creator is an owner); four routes that change the active organization plus mid-session promotion would each need the check (or the middleware would need roles from the session check, a contract change); enrolment waits until the promotion, which opens the takeover window above; needs the same Action anyway. It stays the path if MFA for viewers proves a burden, together with Decision 4's server-side check |
| (c) An Auth0 Action that knows the role | Roles are ours (ADR-0024 §6, ADR-0029): the Action would call the policy service from Auth0 (a secret in the Action, our availability on Auth0's path) or read roles mirrored into `app_metadata` (a second source of truth). The organization is chosen after sign-in, so the Action cannot know which role applies. ADR-0033 §8 keeps authorization logic out of identity hooks |
| Tenant policy *Always* alone, no Action, no console check | Nothing proves the factor to the console: a remembered browser skips the challenge, and a policy switched to *Never* in the dashboard goes unnoticed. The console check makes a misconfiguration fail closed |
| Policy *Always* plus native `acr_values`, no Action | `acr_values` is documented to override a remembered browser, but whether *Always* challenges again when an Auth0 session is reused is not documented; without a challenge there is no `amr` and the person cannot sign in. The Action's `allowRememberBrowser: false` is documented to prompt every time |
| `prompt=login` or `max_age=0` instead of the Action | Forces the password each time, but the second factor can still be skipped by a remembered browser, so `amr` may still be missing |
| An Action that emits a namespaced claim from `event.authentication.methods` (MFA once per Auth0 session, fewer prompts) | Replaces the standard `amr` with a vendor-specific claim (ADR-0024 §1) and is untested; reconsidered if the prompts at every sign-in prove a burden |
| E-mail or SMS as factors | E-mail is the password-reset channel; SMS is exposed to SIM swap, costs per message and is an Enterprise-tier factor |
| Check MFA in the policy service now | See Decision 4: it would re-check the console's own signed statement; the single choke point and the `https` guard cover the same misconfiguration |

## Consequences

- **A paid Auth0 plan before design partners sign in.** Free lists no MFA factors (a forum answer
  says they can be switched on for testing, with upgrade alerts after about three months). B2B
  Essentials (A12) is the minimum; after the upgrade, the dashboard shows whether WebAuthn is in
  its tier (`mfa_webauthn`).
- **Order of rollout:** the Terraform application gets the scopes `read:`/`update:guardian_factors`,
  `read:`/`update:mfa_policies`, `read:`/`create:`/`update:`/`delete:actions`; `terraform apply`
  (workspace `dev`, which also serves `umai-beta`); then the console is deployed. A console with
  the check against a tenant without MFA lets nobody in.
- **Friction:** a TOTP code at every console sign-in (the console session lasts at most 60 minutes
  idle and 12 hours), no remembered browsers, for viewers too. Everyone with a dev-tenant account,
  developers included, enrols once; a local console against an issuer without MFA sets
  `UMAI_CONSOLE_MFA=off`.
- **An Auth0 outage of MFA blocks console sign-in** (fail closed); the engine API and the gateway
  do not depend on it.
- **Answers Q41** (MFA for the owner before the first API key, or a grace period): before, because
  enrolment happens at sign-up, before an organization exists.
- **Factor resets are a support process**, not a product feature, at Stage 0; Auth0 keeps logs
  for days only (1 on Free, 5 on Essentials), so support records each reset in the operations log
  until log streaming exists (ADR-0030).
- **Still open:** adding or replacing a factor oneself; an "MFA factor added or removed" e-mail
  (design E6); ending a person's console sessions after a reset (ADR-0024 §2 amendment, open
  points); Keycloak's `amr` values; Enterprise SSO (S2), where the customer's IdP does the second
  factor and Auth0's own challenge would ask twice: the step-up and server-side variant of
  Decision 4 is designed then.
- **Launch plan S0-6** becomes partial: built and planned (`terraform plan`: 3 to add), open until
  the plan upgrade, the apply, the console deployment and the evidence below.

## Validation

- Console tests (`umai-control-center`): `tests/auth.test.mjs` (the setting and its `https`
  guard, `acr_values` in the authorization URL built by openid-client, only `amr` with `mfa`
  counts), `tests/auth0-action.test.mjs` (the Action's own file against a fake event and api).
- `terraform plan` (workspace `dev`): `auth0_guardian.mfa`, `auth0_action.mfa_on_request`,
  `auth0_trigger_actions.post_login` to add, nothing else changed.
- After the apply (evidence for S0-6): a screenshot of Security → Multi-factor Auth (policy
  *Always*, the factors) and of the post-login trigger; a browser run on the dev tenant: sign up
  → TOTP enrolment with a recovery code → console; sign out and in again → TOTP asked, no
  "remember this browser"; a recovery-code sign-in; with the Action unbound, the console refuses
  the sign-in (`?error=mfa`), then bound again.
- Revisit Decision 1 if viewers or invited reviewers report the prompts as a reason not to use
  the console, or at Enterprise SSO (S2). Revisit the Action if Auth0 documents `acr_values` as
  native and `amr` on reused sessions.
