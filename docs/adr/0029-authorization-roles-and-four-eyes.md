# ADR-0029 — Authorization: tenant roles enforced in the services, four-eyes for production activation

- **Status:** Proposed (awaits product-owner approval). Implemented 2026-10-19: §1 roles from the on-behalf-of assertion, §3 permission matrix (`control_plane/authz.py`, route test), §2 admin-without-rbac rule, §6 deny by default; §4 four-eyes and §5 break-glass open
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner (role names and which edition gets RBAC are product decisions)
- **Builds on:** ADR-0018 (RBAC is an Enterprise capability), ADR-0021 (company → project → guardrail), ADR-0022 (publish, activate), ADR-0023 (on-behalf-of assertion), policy-lifecycle "Approve" stage

## Context

The console has no login and therefore no roles; the policy service accepts any publish or
activation from any holder of its token. AGENTS rule 3 — *models classify, code authorizes* —
applies to people as much as to models: who may change live policy must be decided by
deterministic code in the service that performs the change, not by which buttons a page shows.
Enterprise buyers expect role-based access and separation of duties for production changes;
`docs/architecture/policy-lifecycle.md` already names a four-eyes approval stage.

## Decision

1. **Roles are per tenant and evaluated by the policy service and the engine's control
   endpoints**, from the roles claim of the on-behalf-of assertion (ADR-0023). The console
   hides what a user may not do; the services refuse it. API keys (workload plane) have scopes,
   not roles; a key never holds a human role.
2. **Role set.**

   | Role | May |
   |---|---|
   | `owner` | Everything below, plus delete the tenant, transfer ownership, break-glass |
   | `admin` | Manage members and roles, API keys, SSO/SCIM settings, retention, SIEM export |
   | `policy_editor` | Create and edit drafts, run tests, publish a version (not activate in production) |
   | `approver` | Activate or roll back a production guardrail version; must differ from the version's publisher when approval is required |
   | `viewer` | Read guardrails, versions, decisions, usage, audit log; use the playground against non-production guardrails |
   | `billing` | Plan, invoices, usage; nothing else |

   Edition gating (`rbac` entitlement, ADR-0019 §6): **Community** exposes `owner`, `admin`
   and `viewer` (product owner, 2026-10-19, Q39 → A14; an admin holds every non-owner
   permission); **Enterprise** exposes the full set. Roles are additive; a member may hold
   several. Roles are read from the tenant store by the service (ADR-0033 §1).
3. **Permission matrix as code.** A single table in `control_plane/authz.py` maps
   `(action, resource type) → required roles`; every mutating endpoint declares its action.
   A test enumerates the routes and fails when an endpoint has no declared action.
   Project-level scoping (ADR-0021 hierarchy) is a restriction list on a membership
   (`projects: [...]`, empty = all); a relationship-based engine (Cerbos, OpenFGA) is adopted
   only when the matrix stops being a table.
4. **Four-eyes for production.** A tenant setting `approval_required` (Enterprise default
   **on**, Community **off**) means: a publish creates a version that is **not** active;
   activation of a guardrail marked `production` requires a principal with `approver` whose
   `sub` differs from the version's publisher. The policy service enforces this in
   `activate`, not the console. Non-production guardrails (`environment: test`) can be
   published and activated by a `policy_editor`.
5. **Break-glass.** An `owner` may activate without a second person by supplying a reason;
   the audit event is marked `break_glass`, the tenant's admins are notified, and the console
   shows the banner until an approver acknowledges it. This keeps the control from becoming a
   reason to disable it.
6. **Deny by default.** An action not in the matrix is refused with HTTP 403
   `forbidden`; an assertion without the required role gets the same, and the attempt is an
   audit event (`outcome: denied`).
7. **Self-hosted.** Same roles; the customer's IdP groups map to roles through a configurable
   mapping (ADR-0024). A single-operator install may run with one `owner`; `approval_required`
   is then off by default.

## Alternatives considered

| Option | Why not |
|---|---|
| Gate only in the console UI | Any caller of the service API bypasses it; the token today proves that |
| Per-guardrail ACLs from day one | Complexity before any customer asked; project restriction covers the known need (ADR-0021) |
| Adopt a ReBAC engine now | A six-row table does not justify a new service in the request path of the control plane; revisit when relationships grow |
| Four-eyes as a soft warning | Separation of duties that can be clicked through is not a control an auditor accepts |
| Make RBAC available in Community too | Contradicts the ADR-0018 edition table; the gate is data, so the product owner can flip it without code |

## Consequences

- New tenant-scoped tables `members`, `memberships` (roles, project restriction), tenant
  settings (`approval_required`); console pages for members and roles; an "awaiting approval"
  state in the guardrail page's Versions panel.
- The publish → activate flow in the console gains a second actor; the current one-click
  "Publish and go live" remains available where approval is not required.
- Every denied attempt and every role change is an audit event (ADR-0030): CC6 evidence.

## Validation

- Permission matrix test: each role × each endpoint gives the expected allow/deny; an
  undeclared endpoint fails the suite.
- Four-eyes test: the publisher cannot activate their own production version when
  `approval_required` is on; a different `approver` can; a `policy_editor` cannot.
- Break-glass test: owner activation with a reason succeeds, produces the marked audit event
  and the notification.
- Entitlement test: a Community tenant cannot assign `approver`; an Enterprise tenant can.
- Revisit when project-level roles or customer-defined roles are requested, or when the matrix
  needs relationships (then evaluate Cerbos/OpenFGA with a benchmark).
