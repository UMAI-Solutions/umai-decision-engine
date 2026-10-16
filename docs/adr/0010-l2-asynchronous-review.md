# ADR-0010 — L1 decides; L2 reviews asynchronously

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** product owner (UMAI)
- **Supersedes:** ADR-0002 (L2 synchronous), ADR-0003 (L2 conditional escalation), ADR-0006 (L2 override rules)

## Context

On 2026-10-12 the design moved to a synchronous L2 (ADR-0002). On 2026-10-15 the product
owner chose an asynchronous L2 instead, in which L1's decision is final.

Supporting facts gathered since: gpt-oss-safeguard is "time and compute-intensive" and
OpenAI itself runs it asynchronously behind fast classifiers; hosted options for the 20b
model are thin (Groq "Preview", no Azure serverless); on-prem GPUs below RTX 4090 class miss
a 1.5 s budget (`docs/reference/gpt-oss-safeguard.md`).

## Decision

- **L1 produces the single, final runtime decision.** The caller (browser extension, Agent
  SDK) receives it without waiting for L2.
- Every evaluation emits a **decision event** (tagged action). Events selected by the
  policy's `review_when` rule are delivered durably to the L2 worker.
- **L2 reviews selected events asynchronously**, with the related session history and the
  policy version, and produces an **analysis**; above a threshold it produces a **finding
  candidate** for operator review.
- L2 **never** changes a returned decision, never blocks a caller, and never publishes or
  changes policy without an operator.
- L1 / engine failures follow the guardrail's `on_evaluation_failure`; L2 is not a fallback
  for them. L2 failures never affect runtime decisions.
- Delivery is at-least-once and idempotent per `event_id`: a worker outage must not lose
  events, and redelivery must not create duplicate findings.

## Alternatives considered

| Option | Why not |
|---|---|
| Synchronous L2 for escalated actions (ADR-0002/0003) | Puts a ~1 s generative call on the decision path; weak hosting options; replaced by product owner decision |
| Hybrid (sync for some, async for all) | Two behaviours to build, test and explain; revisit only if evals show L1 alone misses risk that must be blocked in-line |

## Consequences

- Runtime latency is L0 + L1 only. L2 has worker metrics instead: queue age, processing
  time, redelivery and failure rates.
- Multi-step patterns are detected after the fact, as findings — they do not block the step
  that completes the chain. Whether L2 results should influence *later* L1 decisions (e.g. a
  session risk fact) is an open question, not part of this decision.
- In R&D the engine writes to a local, durable event sink (`docs/architecture/l2-llm-analyst.md`);
  event storage, retention and finding ownership beyond R&D are open (Q13).

## Validation

`l2-chains` eval: chain detection rate and false-finding rate on synthetic sessions;
worker metrics under load; idempotency tests for redelivery.
