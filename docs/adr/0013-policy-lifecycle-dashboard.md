# ADR-0013 — Policies are edited and published live from a dashboard

- **Status:** Accepted (direction); not built yet — design constraint for the engine
- **Date:** 2026-10-16
- **Deciders:** product owner (UMAI)

## Context

The product owner wants users to edit and create policies in a dashboard and put them live,
with the updated policy taking effect without redeploying the engine. Development starts
with the API and L0; this capability must be possible later without reworking them.

## Decision

- Lifecycle: **draft → validate → test → preview impact → approve → publish → activate →
  propagate → monitor → (roll back)**. Full description:
  [`../architecture/policy-lifecycle.md`](../architecture/policy-lifecycle.md).
- Drafts, approvals, signing and the active pointer belong to a separate **policy control
  plane** (API + dashboard + draft store + signing key). The engine stays a reader of signed
  bundles (ADR-0012).
- The engine provides what the dashboard needs:
  1. hot reload with atomic swap and last-known-good fallback;
  2. a status endpoint reporting loaded and active versions and load errors;
  3. inline-bundle evaluation for a playground (development/test only);
  4. replay of stored decision events against a candidate bundle (impact preview — possible
     because L1 only uses facts and redacted text, which decision events keep);
  5. shadow evaluation of a candidate bundle on a traffic sample (later).
- Where the control plane lives (a separate service in this repository or its own
  repository) and who signs bundles on-prem are open questions.

## Consequences

- API and L0 work must keep: bundle id + version + hash on every decision, no module-level
  policy caches outside the bundle store, and no policy defaults hard-coded in code.
- L0 changes cannot be impact-previewed on real traffic (it needs raw content); they are
  validated with synthetic test cases only.

## Validation

When the control plane is built: publishing a version reaches all engines within the target
time, rollback is a pointer change, and every engine reports the active version.
