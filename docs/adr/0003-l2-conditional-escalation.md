# ADR-0003 — L2 runs only for escalated actions

- **Status:** Superseded by ADR-0010
- **Date:** 2026-10-12

## Context

With L2 synchronous (ADR-0002), running it on every evaluation would put an LLM call
(~1 s) on all traffic, breaking the clean-path target (p95 ≤ 400 ms) and multiplying cost.

## Decision

L2 runs only when L1 sets `escalate = true`: the L1b action is in the policy's
`escalate_when.actions`, the decision model's confidence is below
`escalate_when.or_confidence_below`, or an L1a rule has `force_escalate`. Everything else
returns after L1.

## Alternatives considered

| Option | Why not |
|---|---|
| L2 on every evaluation | Latency and cost; see context |
| L2 only on L1 `BLOCK` | Misses low-confidence allows, which is where context matters most |

## Consequences

- Escalation rate is a first-class metric, reported per bundle and policy and in every eval.
- Clean actions are still appended to session history so later escalations see them.
- Escalation thresholds become tuning parameters that evals must cover.

## Validation

Eval: escalation rate on realistic traffic samples, and recall of chain scenarios with
conditional vs always-on L2. Revisit if conditional L2 misses chains that always-on catches.
