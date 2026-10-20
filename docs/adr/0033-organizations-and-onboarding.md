# ADR-0033 — Organizations and onboarding: the tenant store owns organizations, the identity service mirrors them

- **Status:** Accepted (product owner approved the Pass 1 design, 2026-10-18). Open product points: Q39–Q44 (Q36–Q38 answered as A11–A13); the slice (b) spike may amend §1–§2 (Consequences)
- **Date:** 2026-10-18
- **Deciders:** engineering proposal; product owner
- **Builds on:** ADR-0019 (entitlements), ADR-0023 (API keys, on-behalf-of assertion), ADR-0024
  (standard OIDC, Auth0 for UMAI Cloud, Keycloak for self-hosted, broker), ADR-0025 (row-level
  security), ADR-0026 (region pinning), ADR-0028 (Community limits), ADR-0029 (roles)
- **Amends:** ADR-0024 §5 (the broker moves to the policy service), ADR-0025 §1–§2 (global
  `users` table; a second, read-only policy on `memberships`)
- **Design:** [`architecture/sign-in-and-onboarding.md`](../architecture/sign-in-and-onboarding.md)
- **Facts:** [`reference/identity-providers.md`](../reference/identity-providers.md), section
  "Sign-in, onboarding and branding" (verified 2026-10-18)

## Context

ADR-0024 §4 says UMAI Cloud has "one Auth0 organization per UMAI tenant" but leaves open who
owns that organization, how people join it, and what happens at the edges: a person in several
organizations, the last owner leaving, an invitation that has expired, a sign-up with a
personal address. Launch-plan stage 1 item 3 asks for one automated flow: sign-up → tenant
record (region chosen) → identity organization → first owner → Community entitlement → first
API key.

What exists on 2026-10-18: the tenant store (`tenants` as a global table, `api_keys` and
`drafts` with row-level security, ADR-0025); tenants and keys created by an operator through
the policy service with the control token (`POST /v1/tenants`, `POST /v1/tenants/{id}/keys`);
console sign-in with standard OIDC against a development Auth0 tenant, without an organization
claim and without roles, so every signed-in user can do everything
(`umai-control-center/docs/sign-in.md`).

Constraints from the brief and earlier decisions: hosted (redirect) login only, never a
password form of our own; standard OIDC in the console, vendor calls only in the broker; roles
and tenant isolation enforced in our services (ADR-0029, ADR-0025); the same model must work
with Keycloak for self-hosted installs; synthetic test users only; no production Auth0 tenant
until Q35 is decided.

The vendor facts this design relies on (organization invitations and their maximum lifetime,
the `organization` authorization parameter and `org_id` claim, invitation links through the
application's login URI, Keycloak organizations and invitations) are in the reference section
named above. Where the reference says *unverified*, this ADR does not depend on it.

## Decision

1. **Organization = UMAI tenant, one to one; our tenant store is the source of truth.**
   - `tenants` gains `idp_org_id` (unique, empty until mirrored), `edition`, `status`
     (`provisioning`, `active`, `suspended`, `deleting`) and `created_by`. The slug stays the
     external `tenant_id` (ADR-0023).
   - Our transaction commits first; the broker then creates the identity organization
     (name = tenant slug, display name = tenant name, metadata = tenant UUID and region) and
     mirrors memberships. A failed mirror leaves the tenant in `provisioning`; a reconcile job
     retries it, and the console shows "setting up" rather than acting without an organization.
   - **Reconciliation** (periodic and on demand): our rows win. An identity organization or
     membership without a row is reported and removed; a missing one is recreated. Nobody edits
     organizations in the Auth0 dashboard, and configuration as code ignores them (§10).
   - **Binding a session to a tenant:** the console reads `org_id` from the validated ID
     token, looks the tenant up by `idp_org_id` (never by `org_name`, a URL segment or a form
     field) and requires an active membership in our store. The token says which organization
     the person signed in to; our store says whether they belong to it. Both are needed.
2. **People and memberships.**
   - One person has one identity per regional identity tenant (`sub`) and one `users` row in
     that regional stack: subject, e-mail, display name, preferred locale, created and last
     sign-in time. Account metadata, not content (ADR-0026 §5). `users` joins the global
     allowlist of ADR-0025 §2.
   - `memberships` is tenant-scoped (row-level security): user, roles (ADR-0029), project
     restriction, state (`active`, `suspended`), invited by, joined at. A person may belong to
     any number of organizations in the region.
   - **"My organizations" before a tenant is chosen:** `memberships` carries a second policy,
     `member_self`, `FOR SELECT USING (user_id = current_setting('app.user_id')::uuid)`. A
     `user_session(user_id)` context sets `app.user_id` and a nil `app.tenant_id`, so it can list
     the person's own memberships and nothing else; writes still need a tenant session. It is
     the only table with a second policy; the schema test checks that the extra policy is
     `SELECT`-only and keyed on `user_id`.
   - A console session is bound to one organization at a time. Switching is a new
     authorization request for the other organization that replaces the session. The
     organization picker is ours (from our store), not the identity service's (Auth0's shows
     only the first 20 organizations).
   - **The only per-issuer difference outside the broker** is declarative: how to ask for an
     organization (Auth0: `organization=<org id>` parameter; Keycloak: scope
     `organization:<alias>`) and where the token names it (Auth0: `org_id`; Keycloak: the
     `organization` claim map). The console reads both from settings; no vendor code.
3. **How people join.** Exactly these paths; there is no "join by organization name" and no
   public list of organizations.
   1. **Create:** a signed-in person with a verified e-mail creates an organization and
      becomes its `owner`.
   2. **Invitation:** an `owner` or `admin` invites an e-mail address with roles. Our
      `invitations` row is the authority; the broker creates the identity service's invitation
      ticket. In UMAI Cloud **we send the e-mail** (Auth0 `send_invitation_email: false`), so the
      template, language and link (our landing page) stay ours; Keycloak sends its own
      `org-invite` e-mail from our theme, because its admin API offers no "do not send"
      option. Accepting requires a pending,
      unexpired, unrevoked invitation **and** a verified e-mail on the accepting identity that
      equals the invited address (trimmed, lower-cased, no plus-address folding), checked by
      our service. Then the membership is written in our store, then mirrored. Opening the link
      while signed in as another account leads to a "wrong account" page offering to sign out
      and continue.
   3. **Verified e-mail domain** (stage 1): an `owner` or `admin` proves a domain with a DNS
      TXT record (`_umai-verification.<domain>`) that our service checks at verification and
      monthly; a lapsed record turns the domain off. Verified domains are mirrored to the
      identity organization (Auth0 discovery domains, Keycloak organization domains) only
      for routing. Per domain the mode is `off` (default),
      `request` (people signing up with that domain see "Ask to join <organization>"; an admin
      approves) or `auto` (they join on first sign-in with the tenant's default role). `owner`
      and `admin` are never assigned automatically, so `auto` exists only where the edition
      offers a lower role (see Consequences). Personal-mail and disposable domains cannot be
      verified; a domain belongs to at most one organization per region.
   4. **Enterprise SSO** (stage 2, ADR-0024 §8): membership through the organization's
      enterprise connection, roles through the group mapping of ADR-0029 §7. Not built now;
      nothing here blocks it.
4. **Personal e-mail addresses are allowed for sign-up.** Developers evaluating Community sign
   up with whatever address they have. Guards, all in our code: the e-mail must be verified
   (standard `email_verified` claim) before a person creates or joins an organization;
   disposable domains are refused at organization creation (a list kept as data); personal-mail
   domains cannot be verified (§3.3); a person may own at most **one** Community organization
   (a setting; more through Enterprise), so the free quota cannot be farmed by creating
   organizations.
5. **Last owner and invitation lifecycle.**
   - Every active organization has at least one active `owner`. A demotion, removal,
     suspension or departure that would leave none is refused with HTTP 409 `last_owner`;
     transferring ownership means promoting another member first. The service enforces this in
     one transaction that locks the tenant's owner memberships (`SELECT … FOR UPDATE`), so two
     owners cannot demote each other at the same moment. A person must leave or hand over their
     organizations before deleting their account; an account removed out of band (support, the
     identity service) puts an organization without owners into `suspended` with a support-led
     recovery.
   - Invitations expire after **7 days**; Enterprise tenants may choose 1–30 days (Auth0's
     default and maximum ticket lifetime). Keycloak's invitation lifetime is the realm-wide
     admin-initiated action lifespan (default 12 h), so the self-hosted realm sets it to 7 days
     and our store enforces the tenant's choice on acceptance. They are
     single use and revocable; "resend" revokes the old invitation and issues a new one.
     Expired, revoked and used links lead to a page that names the organization and says how
     to get a new invitation. Limits are data: pending invitations per tenant (proposed
     Community 20, Enterprise 500) and invitations sent per tenant per day (proposed 50),
     because invitation mail spends the sending domain's reputation.
6. **Region.**
   - Chosen when the organization is created. Today there is one option, EU (ADR-0026; US
     arrives with the US stack), shown with its consequence: it cannot be changed later
     (ADR-0026 §7). An organization is created on the console of the region it will live in.
   - **How sign-in finds the region:** every regional stack has its own identity tenant and
     login domain; nothing about a person crosses regions. While only the EU stack exists, the
     global entry point redirects to the EU console. When the US stack exists, the global
     sign-in front door (ADR-0026 §2) offers the region and remembers the last choice in a
     cookie; e-mails and invitation links always carry the regional host. An e-mail → region
     lookup (keyed hash in the global directory) is deferred until there are two regions,
     because it is an account-enumeration surface.
   - A person with organizations in two regions therefore has two accounts.
7. **Community entitlements and the first API key at creation.**
   - Creating an organization runs as one flow: tenant row (`edition=community`, region,
     `provisioning`) and owner membership in one transaction → signed Community entitlement
     snapshot with the limits from the plan record (ADR-0019 §3; no code constants) → identity
     mirror (organization and owner) → `active` → starter guardrail copied into the tenant
     (ADR-0019 §2) → the console's Get started page.
   - The first API key (scope `evaluate`, environment `live`, name "Default key") is created
     when the owner asks for it on the Get started page, and shown once next to a ready-to-run
     request. It is never created silently: a secret shown only once must be shown to someone
     who is looking.
   - Each step emits an audit event (ADR-0030); the first successful evaluation per tenant is
     marked so that time from sign-up to first evaluation can be measured.
8. **Where the logic lives.**
   - The **policy service** owns organizations, memberships, invitations and domains: new
     routes under `/v1/organizations`, called by the console with the on-behalf-of assertion
     (ADR-0023 §1) and authorized by the permission matrix (ADR-0029 §3). Contract
     `organizations` 0.1 is written before the code (AGENTS rule 5).
   - **The `IdentityBroker` moves from the console to the policy service** (amends ADR-0024
     §5), because the mirror must follow our database transaction and the reconcile job needs
     the database. The broker gains: create, update and delete organization; add and remove
     member; create and revoke invitation; resend verification e-mail; list organizations and
     members (reconcile only). Implementations: `auth0` (Management API, machine-to-machine
     credentials held as a secret, ADR-0027) and `keycloak` (admin REST API), one shared
     contract test suite. The console keeps standard OIDC only.
   - **Identity-service hooks hold no authorization logic.** An Auth0 Action, if any, may only
     translate a standard OIDC request into Auth0 behaviour and is kept in version control. The
     one foreseen: when the console asks for MFA (`acr_values` for multi-factor), the Action
     calls `api.multifactor.enable` (whether Auth0 honours the parameter natively is
     *unverified*). Who needs MFA, who belongs where and what they may do is decided in our
     services.
9. **Self-hosted (Keycloak)** uses the same model: one realm per installation with
   organizations enabled, one Keycloak organization per tenant (alias = tenant slug),
   invitations through Keycloak's organization invite, the tenant store as the source of truth
   and the `keycloak` broker as the mirror. Keycloak routes by e-mail domain itself; verified
   domains are mirrored to the organization's domains. A single-tenant install with one
   organization skips the organization picker.
10. **Identity configuration as code.** One Auth0 tenant per environment, each described by
    the Auth0 Terraform provider (`auth0/auth0`, v1.59.0 on 2026-10-18; OpenTofu-compatible,
    matching ADR-0026's infrastructure as code), with its own state, applied from CI:
    applications and their URLs, connections, MFA and password policies, branding theme and
    page template, custom texts in Turkish and English, e-mail provider and templates, custom
    domain, Actions. Organizations, memberships and invitations are runtime data owned by the
    policy service and are never declared in Terraform. Keycloak's realm (client, themes, SMTP,
    organizations, locales, invitation lifespan) is realm JSON imported at first start; because
    that import skips existing realms, updates go through keycloak-config-cli or the admin API
    (choice at the first self-hosted release, when its support for the shipped Keycloak version
    is checked).

## Alternatives considered

| Option | Why not |
|---|---|
| Auth0 as the source of truth for organizations and memberships (read through the Management API) | Puts vendor rate limits and availability on the console's path, breaks parity with Keycloak, and roles must live with us anyway (ADR-0024 §6) |
| No identity organizations at all: one application, tenant chosen inside the console | Loses organization-scoped login, the hosted invitation flow, per-organization branding and, at stage 2, per-organization enterprise connections, which are the reason for buying Auth0 (ADR-0024 §4) |
| One Auth0 tenant per customer | Configuration and cost multiply per customer; no product supports it at hundreds of tenants |
| Join by e-mail domain without proving the domain | Anyone who registers an address at a domain they do not control, or at a lapsed domain, joins a customer's organization |
| Refuse personal e-mail addresses at sign-up | Blocks the developers Community is for; abuse is handled by e-mail verification, disposable-domain refusal and one Community organization per person |
| Create the first API key automatically | A shown-once secret disappears if nobody is looking when it is shown |
| One global identity tenant for all regions | One account per person, but US customers' account metadata would sit in the EU tenant (or the reverse) and one tenant's outage would stop every region; contradicts ADR-0024 §4 and ADR-0026 §5. Reconsidered when the US stack is built |
| Invitations through our own e-mail and acceptance flow only | We would rebuild the hosted sign-up-with-organization flow Auth0 and Keycloak already provide; our row stays authoritative either way |
| Roles as Auth0 organization roles | ADR-0024 §6: roles are ours, so Keycloak (no organization-scoped roles in core) behaves the same |
| Keep the broker in the console (ADR-0024 §5 as written) | Mirror writes would follow a database transaction owned by another service, and the reconcile job would need database access from the console |
| Auth0's own domain verification and discovery (GA 2026-04-28) as the authority for domains | Vendor-only (Keycloak marks domains verified by an administrator, without a DNS proof), and discovery "does not restrict who can sign up"; we prove domains ourselves and mirror verified ones to Auth0 discovery domains when stage 2 needs routing to enterprise connections |
| Auth0's organization picker and `post_login_prompt` | Shows the first 20 organizations only, knows nothing of our membership states, and puts a vendor screen where our store decides |
| Let Auth0 send invitation e-mails | Needs the paid e-mail features for a branded, bilingual template and points the link at the application login URI, which cannot be `localhost`; sending it ourselves reuses our template pipeline |
| Auth0 Deploy CLI instead of Terraform | Simpler and Auth0-maintained, but no plan/drift view and a second infrastructure tool next to the Terraform/OpenTofu of ADR-0026; the facts comparison is in the reference |

## Consequences

- **Data:** `users` (global), `memberships`, `invitations`, `domains` (tenant-scoped, row-level
  security), new `tenants` columns; the schema test learns the `member_self` exception; the
  generated isolation tests cover the new tables (ADR-0025 Validation).
- **Contracts:** `organizations` 0.1 (organization, membership, invitation, domain, errors such
  as `last_owner`, `invitation_expired`, `invitation_email_mismatch`); the on-behalf-of
  assertion (contract `credentials`) gains the active tenant as a required claim.
- **Policy service:** organization, member, invitation and domain routes; the broker with the
  `auth0` adapter first; the reconcile job; audit events for every step; our own notification
  e-mails (member removed, role changed, ownership transferred, join request), which need an
  e-mail sender in the region (design doc, open point).
- **Console:** onboarding pages (verify e-mail, create organization, Get started), organization
  switcher, members and invitations pages, the session bound to one tenant, the MFA step-up
  for owners and admins (ADR-0024 §3).
- **Dependencies before the end-to-end test can pass:** the on-behalf-of assertion (ADR-0023
  §1); the permission matrix (ADR-0029 §3); tenant-owned bundles (ADR-0025 §8) for the starter
  guardrail.
- **Product decisions recorded as open questions:** ADR-0029 §2 gives Community only `owner`
  and `admin`, while launch-plan stage 1 item 5 lists `owner`/`admin`/`viewer`; domain `auto`
  join depends on which is right. One Community organization per person is a proposal.
- **Costs:** Auth0's Free plan caps organizations at 5 and lacks prompt partials, Pro MFA
  factors and (per the pricing page) the e-mail workflow; B2B Essentials (from $150/month,
  three tenants included) covers the development and beta tenants (reference section).
- **First task of slice (b), a spike in the development tenant:** confirm that
  `organization_usage: allow` admits a sign-in without an organization (the vendor docs
  contradict each other) and how an organization switch behaves with `prompt=none`. If `allow`
  does not admit it, the console signs people in without an organization and requests one only
  for invitation acceptance and, at stage 2, enterprise connections; the tenant binding of §1
  then rests on our store alone, and this ADR is amended.
- **MFA assurance across switches:** Auth0 omits `amr` after silent authentication, so the
  console keeps the MFA time from the last interactive sign-in of the same identity-service
  session (`sid`) instead of asking again at every switch.

## Validation

- The end-to-end suite of the design document: sign-up → organization → invitation → second
  user → first evaluate call with the organization's own key, on localhost and on
  `umai-beta`.
- Isolation: generated cross-tenant tests for every new table; `member_self` returns only the
  person's own rows and refuses writes; a token with organization A plus a membership only in B
  is refused; tampered tenant ids in console routes are refused.
- Last owner: removing, demoting or suspending the last owner returns 409; two concurrent
  demotions leave exactly one owner.
- Invitations: expired, revoked, replayed and wrong-e-mail acceptances are refused with their
  own error pages; acceptance creates exactly one membership.
- Reconciliation: deleting an organization in Auth0 recreates it; an extra Auth0 organization is
  reported; a failed mirror leaves `provisioning` and is retried.
- Metric: time from sign-up to the first successful evaluate call, measured per tenant from
  audit events; five first-time users complete onboarding without help.
- Revisit when the US stack is built (cross-region accounts), when SSO and SCIM arrive (stage
  2), or when organization limits or Management API rate limits of the chosen Auth0 plan
  become a constraint.
