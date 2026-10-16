# ADR-0002 — L2 runs synchronously

- **Status:** Superseded by ADR-0010
- **Date:** 2026-10-12
- **Deciders:** product owner (UMAI)

## Context

An earlier proposal (platform RFC-4, rev. 1) made L2 asynchronous: it would analyse
tagged actions in correlation windows after the decision and produce findings. The product
owner rejected this: L2 must take part in the decision itself.

## Decision

L2 is part of the synchronous evaluation. When L2 runs, the response waits for it (or for
its timeout). Session history is read synchronously so multi-step patterns are judged at
request time.

## Alternatives considered

| Option | Why not |
|---|---|
| Asynchronous L2 producing findings only | Rejected by product owner: detection must influence the decision |
| Hybrid (sync for some tenants, async for others) | Two behaviours to build, test and explain; revisit only if escalation-path latency proves unworkable |

## Consequences

- An escalation path with its own latency target (p95 ≤ 2 500 ms, L2 timeout 1 500 ms).
- L2 failure needs an explicit policy (`l2.on_failure`).
- Session history must be readable in < 10 ms.
- Escalation rate becomes the main cost/latency lever (see ADR-0003).

## Validation

Escalation-path latency measured from the target region in F0/F3; if p95 cannot meet the
target with acceptable accuracy, raise a superseding ADR.
