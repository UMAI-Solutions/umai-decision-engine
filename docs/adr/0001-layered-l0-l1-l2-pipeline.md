# ADR-0001 — Layered L0 → L1 → L2 pipeline

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** product owner (UMAI)

## Context

`umai-engine` evaluates a guardrail as: preflight regex → AGT rules → heuristic and
context-aware (LLM) policies in parallel → most restrictive wins. Three gaps:

1. PII detection is regex-only: no checksum validation, no scores, no redaction model.
2. The LLM judge reads raw text; who/where/what-action context is not structured input,
   and every LLM policy costs an LLM call.
3. Each request is judged alone; multi-step leaks are invisible.

## Decision

Detection is three ordered layers, each consuming the previous layer's structured output:

- **L0** entity detection (what is in the content),
- **L1** policy decision in context (L1a deterministic rules, L1b decision model),
- **L2** LLM analyst over L1's tagged action plus session history.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep parallel policies, add Presidio as one more policy | Does not fix gap 2 or 3; models still read raw text |
| Single large LLM judge with all context | Latency and cost on every request; no deterministic guarantees |

## Consequences

- New contracts between layers (`docs/contracts/`).
- Layer ordering means early exits are possible and latency is additive; per-layer budgets
  are required.
- `umai-engine` behaviours must be mapped onto layers for the eventual cutover.

## Validation

Eval suites per layer (`docs/evals/`) show improvement over the `umai-engine` baseline
without breaking the runtime latency target. (L2's timing is defined by ADR-0010.)
