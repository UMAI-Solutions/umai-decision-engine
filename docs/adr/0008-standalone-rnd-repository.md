# ADR-0008 — Standalone R&D repository, English documentation

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** product owner (UMAI)

## Context

The new engine may eventually replace `umai-engine`, but it is an R&D project first.

## Decision

- `umai-decision-engine` is its own repository (`UMAI-Solutions/umai-decision-engine`),
  developed standalone with no runtime dependency on other UMAI components.
- The knowledge base is self-contained in `docs/`, so agents and engineers can work with
  only this repository checked out. Platform facts needed for the future cutover are
  summarised in `docs/platform-context/`.
- Documentation, code and commit messages are in English.

**Clarification 2026-10-15:** this is a completely separate project with no connection to
`umai-platform` for now. Nothing is read from, written to or linked into that repository.

## Alternatives considered

| Option | Why not |
|---|---|
| Develop inside `umai-engine` | Couples R&D churn to a component in production use |
| Develop inside the platform repository | Against the one-repo-per-component rule |

## Consequences

- The cutover (compat endpoint `/internal/ai-engine/v1/evaluate`, bundle conversion from
  `GuardrailSnapshot`) is a later, explicit phase (F4).
- `platform-context/` must be refreshed when `umai-engine` changes.

## Validation

If the engine replaces `umai-engine` (F4), existing callers can use it through the compat
adapter with no change to their request/response models.
