# Contract: organizations

**Version:** 0.5 · **Producer → consumer:** policy service ↔ console (organizations, memberships,
invitations, verified domains) · **Decisions:** [ADR-0033](../adr/0033-organizations-and-onboarding.md),
[ADR-0029](../adr/0029-authorization-roles-and-four-eyes.md) · **Design:**
[`architecture/sign-in-and-onboarding.md`](../architecture/sign-in-and-onboarding.md)

An **organization** is the sign-in face of a tenant, one to one (ADR-0033 §1). The policy
service owns these records in the tenant store; the identity service (Auth0, Keycloak) holds a
mirror. Every route is called by the console with the service credential and an on-behalf-of
assertion naming the person ([`credentials.md`](credentials.md)); roles are checked by the
permission matrix (ADR-0029 §3), with the person's roles read from the tenant store (never from
the assertion). Models: `src/umai_decision_engine/contracts/organizations.py`; routes:
`control_plane/app.py`, rules: `control_plane/organizations.py`.

**Store first (0.2, ADR-0033 amendment of 2026-10-19).** Until the identity-service mirror exists,
invitations are our own single-use links and the console chooses the active organization; no
identity-service identifiers appear in these shapes.

## Roles

`owner`, `admin`, `policy_editor`, `approver`, `viewer`, `billing` (ADR-0029 §2). A membership
holds one or more. Without the `rbac` entitlement (Community) the roles that can be given are
`owner`, `admin` and `viewer` (Q39, answered A14), and `admin` carries every permission except the
owner-only ones. Only an owner grants, removes or invites an owner; the rule is checked on
the organization's memberships after they are locked, so a concurrent grant cannot slip past it
(0.3).

## Organization — `Organization`

```json
{
  "id": "acme-security",
  "name": "Acme Security",
  "region": "eu",
  "edition": "community",
  "status": "active",
  "created_at": "2026-10-19T09:00:00Z"
}
```

| Field | Notes |
|---|---|
| `id` | The tenant slug (`tenant_id` of the evaluation API and the key registry). 3–40 characters `a-z 0-9 -`, starting and ending with a letter or digit; also the identity organization's `name` |
| `name` | Display name, 2–100 characters |
| `region` | `eu`, `us` or `sh` (self-hosted); chosen at creation, never changed (ADR-0026 §7) |
| `edition` | `community`, `pro` (paid monthly, 0.4) or `enterprise`; entitlements are data (ADR-0019), this field only names the plan; it changes through billing ([billing](billing.md)) or the operator |
| `status` | `provisioning` (identity mirror pending), `active`, `suspended` (no active owner, or by UMAI), `deleting` |

## My organizations — `MyOrganization`

What `GET /v1/me/organizations` returns for the switcher: the person's own memberships only.

```json
{
  "id": "acme-security",
  "name": "Acme Security",
  "region": "eu",
  "status": "active",
  "roles": ["owner"]
}
```

## Membership — `Membership`

```json
{
  "tenant_id": "acme-security",
  "user": { "id": "1b7c2f4e-5a8d-4d0e-9b61-2f3a4c5d6e7f", "email": "ada@example.com", "name": "Ada Example" },
  "roles": ["admin"],
  "projects": [],
  "state": "active",
  "invited_by": "6f1e2d3c-4b5a-4968-8776-5a4b3c2d1e0f",
  "joined_at": "2026-10-19T09:30:00Z"
}
```

`user.id` is our user id (a UUID), never the identity service's subject. `projects` restricts the
membership to those projects (ADR-0029 §3; empty = all). `state` is `active` or `suspended`.

## Invitation — `Invitation`

```json
{
  "id": "0c9b8a7d-6e5f-4a3b-8c2d-1e0f9a8b7c6d",
  "tenant_id": "acme-security",
  "email": "grace@example.com",
  "roles": ["viewer"],
  "state": "pending",
  "invited_by": "1b7c2f4e-5a8d-4d0e-9b61-2f3a4c5d6e7f",
  "created_at": "2026-10-19T10:00:00Z",
  "expires_at": "2026-10-26T10:00:00Z",
  "accepted_at": null,
  "revoked_at": null
}
```

`email` is stored normalised (trimmed, lower-cased; plus-addresses are not folded). `state` is
`pending`, `accepted`, `revoked` or `expired` (derived from `expires_at`). Default lifetime 7
days; 1–30 days when the tenant chooses (ADR-0033 §5).

## Verified domain — `Domain`

```json
{
  "id": "4d3c2b1a-0f9e-4d8c-b7a6-958473625140",
  "tenant_id": "acme-security",
  "domain": "acme.example",
  "status": "verified",
  "mode": "request",
  "default_role": "viewer",
  "verification": { "record_name": "_umai-verification.acme.example", "record_value": "umai-verify=6Jf2kQx9pW3m" },
  "verified_at": "2026-10-19T11:00:00Z"
}
```

`status`: `pending` (TXT record not seen yet), `verified`, `lapsed` (record gone at a monthly
check; the mode falls back to `off`). `mode`: `off`, `request`, `auto`; `auto` needs a
`default_role` other than `owner` and `admin` (ADR-0033 §3.3).

## Requests

`OrganizationCreate` (`POST /v1/organizations`):

```json
{ "name": "Acme Security", "id": "acme-security", "region": "eu", "accept_terms": true, "terms_version": "2026-10" }
```

`accept_terms` must be `true`; `region` must be the stack's region (one option per console).
The organization, the creator's owner membership and the accepted terms (`terms_version`, the
person, the time; recorded with the organization, not returned) are written in one transaction
that also enforces the Community limit, so concurrent creations cannot pass it and no
organization exists without its owner (0.3). The response is `{"organization": Organization, "starter_guardrail": {"id", "version"} | null}`:
the deployment's starter guardrail (`UMAI_DE_STARTER_GUARDRAIL`) is copied into the new
organization as its version 1, signed for it. `InvitationCreate`
(`POST /v1/organizations/{id}/invitations`):

```json
{ "email": "grace@example.com", "roles": ["viewer"], "ttl_days": 7 }
```

The response is `{"invitation": Invitation, "token": "<secret>"}`: the link's secret, shown
once and stored as a SHA-256 hash only. The console builds the link
`<console>/invitation?organization=<id>&token=<secret>` (and, once an e-mail sender exists, sends
it); resending revokes the old link and returns a new token.

`MembershipUpdate` `{ "roles": [...] }`; `OrganizationUpdate` `{ "name": "…" }`; `DomainCreate`
`{ "domain": "acme.example" }`; `DomainUpdate` `{ "mode": "request", "default_role": "viewer" }`;
`InvitationAccept` `{ "organization": "<organization id>", "token": "<link secret>" }`.

## Routes

Implemented 2026-10-19 except the domain routes (stage 1).

| Route | Action (permission matrix) | Does |
|---|---|---|
| `GET /v1/me/organizations` | `me.organizations` (self-service) | Switcher |
| `POST /v1/organizations` | `organization.create` (self-service, verified e-mail) | Create: tenant, owner membership, starter guardrail |
| `GET /v1/organizations/{id}` | `organization.read` | Status, name, region, edition |
| `PATCH /v1/organizations/{id}` | `organization.update` | Display name |
| `GET /v1/organizations/{id}/export.zip` | `organization.export` (owners only) | The organization archive ([data-export](data-export.md) §3, 0.5) |
| `GET /v1/organizations/{id}/members` | `member.read` | List |
| `PATCH /v1/organizations/{id}/members/{user}` | `member.update` | Roles |
| `DELETE /v1/organizations/{id}/members/{user}` | `member.remove` | Remove |
| `POST /v1/organizations/{id}/leave` | `member.leave` (self-service) | Leave |
| `POST/GET /v1/organizations/{id}/invitations`, `DELETE …/{inv}`, `POST …/{inv}/resend` | `invitation.manage` | Invite, list, revoke, resend |
| `POST /v1/invitations/accept` | `invitation.accept` (self-service) | Accept |
| `POST /v1/tenants/{id}/keys` (credentials) | `key.create` | The organization's API keys, including the first |
| `PATCH /v1/tenants/{id}/keys/{key_id}` (credentials 0.9) | `key.update` | A key's allowed networks |
| `POST/GET /v1/organizations/{id}/domains`, `POST …/{d}/verify`, `PATCH …/{d}` | `domain.manage` | Verified domains |

## Errors

Bodies are `{ "detail": { "error": "<code>", "detail": "<sentence>" } }` (FastAPI's `detail`
wrapper, like every policy-service refusal).

| HTTP | `error` | When |
|---|---|---|
| 403 | `actor_required` | a self-service action without a person (the control token alone) |
| 403 | `forbidden` | the person's roles do not allow the action (ADR-0029 §6), or a non-owner touches ownership |
| 403 | `email_unverified` | the person's e-mail is not verified (ADR-0033 §4) |
| 403 | `community_limit_reached` | the person already owns a Community organization |
| 403 | `invitation_email_mismatch` | the accepting identity's verified e-mail differs from the invited one |
| 404 | `not_found` | no such organization, member, invitation or domain in this tenant |
| 409 | `id_taken` | the organization id exists (or is reserved) |
| 409 | `last_owner` | the change would leave the organization without an active owner |
| 409 | `invitation_used` | the invitation was already accepted |
| 409 | `domain_taken` | another organization in the region holds the domain |
| 409 | `export_running` | an archive of the organization is being built (0.5) |
| 409 | `invitation_limit_reached` | the tenant has its maximum of pending invitations (default 20) |
| 410 | `invitation_expired`, `invitation_revoked` | the invitation can no longer be accepted |
| 422 | `export_too_large` | the organization holds more records than one archive carries (0.5; [data-export](data-export.md) §4) |
| 422 | `disposable_email_domain` | creating an organization from a disposable address |
| 422 | `personal_domain` | verifying a personal-mail domain |
| 422 | `region_unavailable` | `region` is not this stack's region |
| 422 | `role_unavailable` | a role the tenant's edition does not offer |
| 422 | `email_required` | the assertion carries no e-mail |
| 422 | `email_invalid` | the assertion's e-mail is not an address the contract can show (0.3) |

## Changelog

- 0.5 (2026-10-30, ADR-0047): `GET /v1/organizations/{id}/export.zip`, owners only; errors
  `export_running`, `export_too_large`.
- 0.4 (2026-10-30, ADR-0044): edition `pro`.
- 0.3 (2026-10-20, review fixes): error bodies documented as they are sent (`{"detail": {…}}`);
  `email_invalid`; creation is one transaction (limit, owner membership, terms) and records the
  accepted `terms_version` with the person and the time; ownership rules checked on the locked
  memberships. No request or response shape changes.
- 0.2 (2026-10-19): store first: invitations are our own single-use links
  (`InvitationAccept` = organization + token; invite response carries the token once);
  create response with the starter guardrail; `OrganizationUpdate`; leave route; Community
  roles owner/admin/viewer; new errors. Routes implemented except domains.
- 0.1 (2026-10-19): initial: organization, my organizations, membership, invitation, verified
  domain, requests, planned routes, errors (ADR-0033).
