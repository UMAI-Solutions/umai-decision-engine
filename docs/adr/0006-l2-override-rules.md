# ADR-0006 — What L2 may override

- **Status:** Superseded by ADR-0010 (L2 never changes decisions)
- **Date:** 2026-10-12

## Context

L2 sees more context than L1 (session history, full policy text) and exists partly to
correct decision-model false positives. Deterministic outcomes (L0 hard blocks, L1a rules)
encode explicit customer intent and must stay predictable.

## Decision

- L2 may make any decision **stricter**.
- L2 may make an **L1b** decision **more permissive**; the response and tagged action record
  `l2_override {from, to}`.
- L2 may not go below the **L1a floor** and never runs after an L0 hard block or L1a `BLOCK`.

## Alternatives considered

| Option | Why not |
|---|---|
| L2 can only tighten | L2 then cannot fix decision-model false positives; more user friction |
| L2 always has the last word | Deterministic rules become unreliable; harder to explain to customers |

## Consequences

- Override rate (by direction) is a monitored metric; a high relax rate signals L1b
  thresholds or questions need work.

## Validation

Evals report precision/recall with and without relaxation; product owner confirms the rule.
