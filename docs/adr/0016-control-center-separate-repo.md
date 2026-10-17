# ADR-0016 — The engine's console is a separate repository that calls the API over HTTP with a service token

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** product owner (UMAI)

## Context

The engine needs a user interface: a playground to try inputs against a policy, a view of
loaded bundle versions, and later the policy editor of ADR-0013. The existing UMAI Control
Center lives in `umai-platform` and is built around `umai-engine` / `umai-service`. Adding
the new engine to it would couple this R&D project to the platform, which ADR-0008 and the
product owner ruled out ("no connection for now"). Rewriting the existing Control Center was
also rejected as too large for a feature the engine has not yet earned.

## Decision

1. The console is a **new, separate repository, `umai-control-center`**, with the same visual
   design as the existing Control Center (Next.js, Tailwind, the same tokens and components,
   copied once — no dependency on `umai-platform`).
2. The console calls the engine **only over its HTTP API**, from its server side (Next.js
   route handlers). The browser never calls the engine; the engine URL and token live in the
   console's server environment.
3. The engine requires a **bearer token** (`UMAI_DE_API_TOKEN`) on every `/v1` route; `/healthz`
   and `/readyz` stay open. Outside `development` the API refuses to start without a token.
4. The engine still has no connection to `umai-platform`; this ADR does not change ADR-0008
   for the engine. The console is the engine's first client.

## Alternatives considered

| Option | Why not |
|---|---|
| A new section inside the existing Control Center | Couples the R&D engine to `umai-platform`; needs platform changes the product owner does not want now |
| Rewrite the existing Control Center | Months of work and risk for the existing product, for an engine that has not met its exit criteria |
| Console inside this repository (monorepo) | Simpler contract sharing, but mixes a Python service and a Next.js app in one repo; the product owner chose a separate clean repository |
| Browser calls the engine directly (CORS) | Exposes the engine and its token to browsers and puts raw input on another network path |
| No authentication on the API | Anyone who can reach the port could evaluate content and read bundle status |

## Consequences

- Two repositories evolve together: the console mirrors the evaluation API contract in its
  own TypeScript types. A generated client from the engine's OpenAPI document is the next
  step when the API grows.
- The console's design is a copy: it can drift from the existing Control Center. Acceptable
  for R&D; if the new stack replaces the old one, the old one goes away.
- Mutual TLS or per-user identity (forwarding the console user to the engine for audit) may
  replace the shared token later.

## Validation

- Engine tests: every `/v1` route returns 401 without the right token, health routes stay
  open, and the app refuses to start without a token outside development.
- The console's playground and bundles pages work against a local engine with and without a
  token.
