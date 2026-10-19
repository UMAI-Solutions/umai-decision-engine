# Contract: organizations

**Version:** 0.1 · **Producer → consumer:** policy service ↔ console (organizations, memberships,
invitations, verified domains) · **Decisions:** [ADR-0033](../adr/0033-organizations-and-onboarding.md),
[ADR-0029](../adr/0029-authorization-roles-and-four-eyes.md) · **Design:**
[`architecture/sign-in-and-onboarding.md`](../architecture/sign-in-and-onboarding.md)

An **organization** is the sign-in face of a tenant, one to one (ADR-0033 §1). The policy
service owns these records in the tenant store; the identity service (Auth0, Keycloak) holds a
mirror. Every route is called by the console with the service credential and an on-behalf-of
assertion naming the person ([`credentials.md`](credentials.md)); roles are checked by the
permission matrix (ADR-0029 §3). Models: `src/umai_decision_engine/contracts/organizations.py`.
Routes are planned for slice (b) of the sign-in rework; this version fixes the shapes.

Identity-service identifiers (Auth0 organization id, invitation ticket) are not part of these
shapes, except where the console must hand them back (`InvitationAccept`).

## Roles

`owner`, `admin`, `policy_editor`, `approver`, `viewer`, `billing` (ADR-0029 §2). A membership
holds one or more. Without the `rbac` entitlement, `admin` carries every permission except the
owner-only ones (ADR-0029 §2, "every member is an admin").

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
| `edition` | `community` or `enterprise`; entitlements are data (ADR-0019), this field only names the plan |
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

`accept_terms` must be `true`. `InvitationCreate` (`POST /v1/organizations/{id}/invitations`):

```json
{ "email": "grace@example.com", "roles": ["viewer"], "ttl_days": 7 }
```

`MembershipUpdate` `{ "roles": [...] }`; `DomainCreate` `{ "domain": "acme.example" }`;
`DomainUpdate` `{ "mode": "request", "default_role": "viewer" }`; `InvitationAccept`
`{ "invitation": "<identity-service ticket>", "organization": "<identity-service organization id>" }`.

## Routes (slice (b))

| Route | Action (permission matrix) | Does |
|---|---|---|
| `GET /v1/me/organizations` | the person's own memberships | Switcher |
| `POST /v1/organizations` | a person with a verified e-mail | Create (tenant, owner, entitlement, mirror) |
| `GET /v1/organizations/{id}` | `organization.read` | Status, name, region, edition |
| `PATCH /v1/organizations/{id}` | `organization.update` | Display name |
| `GET /v1/organizations/{id}/members` | `member.read` | List |
| `PATCH /v1/organizations/{id}/members/{user}` | `member.update` | Roles |
| `DELETE /v1/organizations/{id}/members/{user}` | `member.remove` (or the member leaving) | Remove |
| `POST/GET /v1/organizations/{id}/invitations`, `DELETE …/{inv}`, `POST …/{inv}/resend` | `invitation.manage` | Invite, list, revoke, resend |
| `POST /v1/invitations/accept` | the invited person | Accept |
| `POST/GET /v1/organizations/{id}/domains`, `POST …/{d}/verify`, `PATCH …/{d}` | `domain.manage` | Verified domains |

## Errors

Bodies are `{ "error": "<code>", "detail": "<sentence>" }`.

| HTTP | `error` | When |
|---|---|---|
| 403 | `forbidden` | the person's roles do not allow the action (ADR-0029 §6) |
| 403 | `email_unverified` | the person's e-mail is not verified (ADR-0033 §4) |
| 403 | `community_limit_reached` | the person already owns a Community organization |
| 403 | `invitation_email_mismatch` | the accepting identity's verified e-mail differs from the invited one |
| 404 | `not_found` | no such organization, member, invitation or domain in this tenant |
| 409 | `id_taken` | the organization id exists (or is reserved) |
| 409 | `last_owner` | the change would leave the organization without an active owner |
| 409 | `invitation_used` | the invitation was already accepted |
| 409 | `domain_taken` | another organization in the region holds the domain |
| 410 | `invitation_expired`, `invitation_revoked` | the invitation can no longer be accepted |
| 422 | `disposable_email_domain` | creating an organization from a disposable address |
| 422 | `personal_domain` | verifying a personal-mail domain |

## Changelog

- 0.1 (2026-10-19): initial: organization, my organizations, membership, invitation, verified
  domain, requests, planned routes, errors (ADR-0033).
